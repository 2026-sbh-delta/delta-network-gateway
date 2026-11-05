provider "google" {
  project        = var.project_id
  region         = var.region
  zone           = var.zone
  default_labels = { team = "delta", environment = "lab", managed_by = "terraform" }
}
provider "google-beta" {
  project = var.project_id
  region  = var.region
}
data "google_project" "current" { project_id = var.project_id }

locals {
  advertised_vpn_cidrs      = concat(local.advertised_workload_cidrs, sort(tolist(var.platform_database_endpoint_cidrs)))
  project_parent            = "projects/${var.project_id}"
  subnet_map                = { central = var.subnets.central, workload = var.subnets.workload }
  all_private_cidrs         = concat(values(local.subnet_map), values(var.application_subnets), sort(tolist(var.aws_cidrs)))
  advertised_workload_cidrs = concat([var.subnets.workload], [for key in ["backend", "database"] : var.application_subnets[key] if contains(keys(var.application_subnets), key)])
  imported_aws_cidrs        = sort(tolist(setunion(var.aws_cidrs, var.platform_aws_cidrs)))
  cidr_ranges = [for cidr in local.all_private_cidrs : {
    start = try(sum([for i, octet in split(".", cidrhost(cidr, 0)) : tonumber(octet) * pow(256, 3 - i)]), -1)
    end   = try(sum([for i, octet in split(".", cidrhost(cidr, -1)) : tonumber(octet) * pow(256, 3 - i)]), -1)
  }]
  required_apis = toset(["compute.googleapis.com", "networksecurity.googleapis.com", "networkconnectivity.googleapis.com", "pubsub.googleapis.com", "logging.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com", "iap.googleapis.com", "oslogin.googleapis.com", "privateca.googleapis.com", "cloudbilling.googleapis.com", "sqladmin.googleapis.com", "secretmanager.googleapis.com", "dns.googleapis.com", "storage.googleapis.com"])
}

resource "terraform_data" "contract" {
  input = "delta-gcp-central-v1"
  lifecycle {
    precondition {
      condition     = tostring(data.google_project.current.number) == var.project_number
      error_message = "Authenticated project does not match the expected Project Number."
    }
    precondition {
      condition     = startswith(var.zone, "${var.region}-")
      error_message = "Workload and endpoint must be in the selected region."
    }
    precondition {
      condition     = length(var.aws_cidrs) > 0 && alltrue([for cidr in local.all_private_cidrs : try(cidrhost(cidr, 0) == split("/", cidr)[0] && length(split(".", cidrhost(cidr, 0))) == 4 && cidr != "0.0.0.0/0", false)])
      error_message = "Use canonical, non-default IPv4 prefixes."
    }
    precondition {
      condition     = alltrue(flatten([for i, a in local.cidr_ranges : [for j, b in local.cidr_ranges : !(a.start <= b.end && b.start <= a.end) if i < j]]))
      error_message = "Central, workload and AWS networks must not overlap."
    }
    precondition {
      condition = length(var.aws_vpn_tunnels) == 0 || (
        length(var.aws_vpn_tunnels) == 4 &&
        toset([for t in values(var.aws_vpn_tunnels) : t.aws_interface]) == toset([0, 1, 2, 3]) &&
        alltrue([for t in values(var.aws_vpn_tunnels) : t.gcp_interface == floor(t.aws_interface / 2) && t.aws_asn != var.router_asn && t.secret_version >= 1 && try(cidrhost(t.inside_cidr, 1) == t.aws_inside_ip && cidrhost(t.inside_cidr, 2) == t.gcp_inside_ip && split("/", t.inside_cidr)[1] == "30" && startswith(t.inside_cidr, "169.254."), false)]) &&
        length(distinct([for t in values(var.aws_vpn_tunnels) : t.inside_cidr])) == 4 &&
        length(distinct([for t in values(var.aws_vpn_tunnels) : t.outside_ip])) == 4
      )
      error_message = "Supply the complete unique four-tunnel handoff with interfaces 0/0/1/1 and valid /30 peers."
    }
  }
}

resource "google_project_service" "required" {
  for_each           = local.required_apis
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
  depends_on         = [terraform_data.contract]
}

# Shared project service agent is also used by the platform log pipeline.
# Keep its Terraform address while retiring the network-only exporter.
resource "google_project_service_identity" "pubsub" {
  provider   = google-beta
  project    = var.project_id
  service    = "pubsub.googleapis.com"
  depends_on = [google_project_service.required]
}
