resource "google_pubsub_topic" "network" {
  name       = "${var.name}-network-security"
  depends_on = [google_project_service.required]
}
resource "google_pubsub_topic" "dead_letter" {
  name       = "${var.name}-network-security-dlq"
  depends_on = [google_project_service.required]
}
resource "google_pubsub_subscription" "collector" {
  name                       = "${var.name}-aws-collector"
  topic                      = google_pubsub_topic.network.id
  ack_deadline_seconds       = 60
  message_retention_duration = "604800s"
  expiration_policy { ttl = "" }
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "60s"
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 10
  }
}
resource "google_pubsub_subscription" "dead_letter" {
  name                       = "${var.name}-network-security-dlq"
  topic                      = google_pubsub_topic.dead_letter.id
  message_retention_duration = "604800s"
  expiration_policy { ttl = "" }
}
resource "google_logging_project_sink" "network" {
  name                   = "${var.name}-network-security"
  destination            = "pubsub.googleapis.com/${google_pubsub_topic.network.id}"
  unique_writer_identity = true
  filter                 = "logName=(\"projects/${var.project_id}/logs/compute.googleapis.com%2Ffirewall\" OR \"projects/${var.project_id}/logs/networksecurity.googleapis.com%2Ffirewall_threat\" OR \"projects/${var.project_id}/logs/compute.googleapis.com%2Fvpn\" OR \"projects/${var.project_id}/logs/compute.googleapis.com%2Frouter\")"
}
resource "google_pubsub_topic_iam_member" "log_writer" {
  topic  = google_pubsub_topic.network.name
  role   = "roles/pubsub.publisher"
  member = google_logging_project_sink.network.writer_identity
}
resource "google_project_service_identity" "pubsub" {
  provider   = google-beta
  project    = var.project_id
  service    = "pubsub.googleapis.com"
  depends_on = [google_project_service.required]
}
resource "google_pubsub_topic_iam_member" "dlq_writer" {
  topic  = google_pubsub_topic.dead_letter.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
resource "google_pubsub_subscription_iam_member" "dlq_forwarder" {
  subscription = google_pubsub_subscription.collector.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
resource "google_iam_workload_identity_pool" "aws" {
  workload_identity_pool_id = "${var.name}-aws"
  display_name              = "Delta AWS network log collector"
  depends_on                = [google_project_service.required]
}
locals {
  collector_account   = split(":", var.aws_collector_role_arn)[4]
  collector_role      = basename(var.aws_collector_role_arn)
  collector_principal = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.aws.name}/attribute.aws_role/${local.collector_role}"
}
resource "google_iam_workload_identity_pool_provider" "aws" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.aws.workload_identity_pool_id
  workload_identity_pool_provider_id = "aws-collector"
  attribute_mapping = {
    "google.subject"     = "assertion.arn"
    "attribute.aws_role" = "assertion.arn.extract('assumed-role/{role}/')"
  }
  attribute_condition = "assertion.account == '${local.collector_account}' && assertion.arn.startsWith('arn:aws:sts::${local.collector_account}:assumed-role/${local.collector_role}/')"
  aws { account_id = local.collector_account }
}
resource "google_pubsub_subscription_iam_member" "collector" {
  subscription = google_pubsub_subscription.collector.name
  role         = "roles/pubsub.subscriber"
  member       = local.collector_principal
}
resource "google_pubsub_subscription_iam_member" "dlq_reader" {
  subscription = google_pubsub_subscription.dead_letter.name
  role         = "roles/pubsub.subscriber"
  member       = local.collector_principal
}
