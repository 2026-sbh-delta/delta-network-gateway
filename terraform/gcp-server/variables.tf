variable "project_id" {
  description = "Existing, authorized GCP project ID. No project or billing account is created."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid GCP project ID."
  }
}
variable "project_number" {
  description = "Expected project number, compared with the live project."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{6,20}$", var.project_number))
    error_message = "project_number must be a numeric GCP project number."
  }
}
variable "name" {
  description = "Resource and network tag prefix."
  type        = string
  default     = "delta-lab"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,24}[a-z0-9]$", var.name))
    error_message = "name must be 2-26 lowercase resource name characters."
  }
}
variable "region" {
  description = "GCP region containing the subnet, router and HA VPN gateway."
  type        = string
  default     = "asia-northeast3"
}
variable "zone" {
  description = "Zone for the single server VM."
  type        = string
  default     = "asia-northeast3-a"
  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region)) && can(regex("^${var.region}-[a-z]$", var.zone))
    error_message = "zone must be in region."
  }
}
variable "address_space" {
  description = "Reserved private IPv4 /16 address plan, not a GCP VPC-wide CIDR resource."
  type        = string
  validation {
    condition = try(cidrnetmask(var.address_space) != "" && split("/", var.address_space)[1] == "16" && cidrhost(var.address_space, 0) == split("/", var.address_space)[0] && (
      startswith(var.address_space, "10.") || startswith(var.address_space, "192.168.") || (startswith(var.address_space, "172.") && tonumber(split(".", var.address_space)[1]) >= 16 && tonumber(split(".", var.address_space)[1]) <= 31)
    ), false)
    error_message = "address_space must be a canonical RFC1918 IPv4 /16."
  }
}
variable "server_subnet_cidr" {
  description = "Actual regional server subnet, a canonical IPv4 /24 within address_space."
  type        = string
  validation {
    condition     = try(cidrnetmask(var.server_subnet_cidr) != "" && split("/", var.server_subnet_cidr)[1] == "24" && cidrhost(var.server_subnet_cidr, 0) == split("/", var.server_subnet_cidr)[0] && cidrhost("${split("/", var.server_subnet_cidr)[0]}/16", 0) == cidrhost(var.address_space, 0), false)
    error_message = "server_subnet_cidr must be a canonical /24 inside address_space."
  }
}
variable "server_private_ip" {
  description = "Fixed server IPv4; excludes the four GCP reserved subnet addresses."
  type        = string
  validation {
    condition     = try(cidrnetmask("${var.server_private_ip}/32") != "" && cidrhost("${var.server_private_ip}/24", 0) == cidrhost(var.server_subnet_cidr, 0) && tonumber(split(".", var.server_private_ip)[3]) >= 2 && tonumber(split(".", var.server_private_ip)[3]) <= 253, false)
    error_message = "server_private_ip must be a usable host in server_subnet_cidr."
  }
}
variable "server_image_name" {
  description = "Pinned ready Ubuntu 24.04 standard AMD64 image name in ubuntu-os-cloud. Resolve before a real plan."
  type        = string
  validation {
    condition     = can(regex("^ubuntu-2404-noble-amd64-v[0-9]+$", var.server_image_name))
    error_message = "Use a pinned standard Ubuntu 24.04 AMD64 image name, not a family or minimal image."
  }
}
variable "connected_cidrs" {
  description = "Known AWS/other connected IPv4 networks for overlap checking, not routes."
  type        = set(string)
  validation {
    condition = length(var.connected_cidrs) > 0 && alltrue([for cidr in var.connected_cidrs : try(
      cidrnetmask(cidr) != "" && cidrhost(cidr, 0) == split("/", cidr)[0] && tonumber(split("/", cidr)[1]) >= 8 && tonumber(split("/", cidr)[1]) <= 32, false
    )])
    error_message = "connected_cidrs must contain canonical IPv4 prefixes of /8 or narrower."
  }
}
variable "aws_client_cidrs" {
  description = "AWS client sources allowed to reach both TCP test ports. Prefer the single client /32."
  type        = set(string)
  validation {
    condition = length(var.aws_client_cidrs) > 0 && alltrue([for cidr in var.aws_client_cidrs : try(
      cidrnetmask(cidr) != "" && cidrhost(cidr, 0) == split("/", cidr)[0] && tonumber(split("/", cidr)[1]) >= 16, false
    )])
    error_message = "aws_client_cidrs must be canonical IPv4 prefixes of /16 or narrower."
  }
}
variable "router_asn" {
  description = "Private Cloud Router ASN; must differ from every AWS peer ASN."
  type        = number
  default     = 65001
  validation {
    condition     = floor(var.router_asn) == var.router_asn && ((var.router_asn >= 64512 && var.router_asn <= 65534) || (var.router_asn >= 4200000000 && var.router_asn <= 4294967294))
    error_message = "router_asn must be a private 16-bit or 32-bit ASN."
  }
}
variable "vpn_tunnels" {
  description = "Empty for the foundation, or exactly four AWS tunnel contracts. No PSKs."
  type = map(object({
    gcp_interface  = number
    aws_interface  = number
    aws_outside_ip = string
    inside_cidr    = string
    gcp_inside_ip  = string
    aws_inside_ip  = string
    aws_bgp_asn    = number
    secret_version = optional(number, 1)
  }))
  default = {}
  validation {
    condition = length(var.vpn_tunnels) == 0 || (length(var.vpn_tunnels) == 4 &&
      toset([for t in values(var.vpn_tunnels) : t.aws_interface]) == toset([0, 1, 2, 3]) &&
      alltrue([for key, t in var.vpn_tunnels : can(regex("^[a-z][a-z0-9-]{0,24}$", key)) && t.gcp_interface == floor(t.aws_interface / 2)]) &&
    length(distinct([for t in values(var.vpn_tunnels) : t.aws_outside_ip])) == length(var.vpn_tunnels))
    error_message = "Supply zero or four uniquely addressed tunnels: AWS interfaces 0/1 -> GCP 0 and 2/3 -> GCP 1."
  }
  validation {
    condition = alltrue([for t in values(var.vpn_tunnels) : try(
      cidrnetmask(t.inside_cidr) != "" && startswith(t.inside_cidr, "169.254.") && split("/", t.inside_cidr)[1] == "30" && cidrhost(t.inside_cidr, 0) == split("/", t.inside_cidr)[0] &&
      !contains(["169.254.0.0/30", "169.254.1.0/30", "169.254.2.0/30", "169.254.3.0/30", "169.254.4.0/30", "169.254.5.0/30", "169.254.169.252/30"], t.inside_cidr) &&
      t.aws_inside_ip == cidrhost(t.inside_cidr, 1) && t.gcp_inside_ip == cidrhost(t.inside_cidr, 2) &&
      t.aws_bgp_asn != var.router_asn && floor(t.aws_bgp_asn) == t.aws_bgp_asn && ((t.aws_bgp_asn >= 64512 && t.aws_bgp_asn <= 65534) || (t.aws_bgp_asn >= 4200000000 && t.aws_bgp_asn <= 4294967294)) &&
      floor(t.secret_version) == t.secret_version && t.secret_version >= 1, false
    )]) && length(distinct([for t in values(var.vpn_tunnels) : t.inside_cidr])) == length(var.vpn_tunnels) && length(distinct([for t in values(var.vpn_tunnels) : t.aws_bgp_asn])) <= 1
    error_message = "Use unique non-reserved link-local /30s, AWS host .1 / GCP host .2, one distinct private AWS ASN and positive secret versions."
  }
}
variable "tunnel_shared_secrets" {
  description = "Ephemeral AWS PSKs keyed exactly like vpn_tunnels; sent only to write-only provider fields."
  type        = map(string)
  default     = {}
  sensitive   = true
  ephemeral   = true
  validation {
    condition     = toset(keys(var.tunnel_shared_secrets)) == toset(keys(var.vpn_tunnels)) && alltrue([for psk in values(var.tunnel_shared_secrets) : length(psk) >= 8 && length(psk) <= 64 && can(regex("^[A-Za-z0-9][A-Za-z0-9._]*$", psk)) && !startswith(psk, "0")])
    error_message = "PSK keys must exactly match tunnel keys and values must satisfy AWS PSK constraints."
  }
}
