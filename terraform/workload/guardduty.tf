# This detector covers the account/Region, independently of application and ANF modes.
# Import any existing detector before applying; AWS permits only one per account/Region.
resource "aws_guardduty_detector" "account" {
  enable                       = var.guardduty_enabled
  finding_publishing_frequency = var.guardduty_finding_publishing_frequency
  tags                         = { Name = "${var.name}-${var.environment}-guardduty" }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.contract]
}

# Deleting a feature resource only removes Terraform ownership. Change status to disable.
resource "aws_guardduty_detector_feature" "protection" {
  for_each = { for name, feature in var.guardduty_features : name => feature if feature != null }

  detector_id = aws_guardduty_detector.account.id
  name        = each.key
  status      = each.value.status

  dynamic "additional_configuration" {
    for_each = each.value.additional_configuration
    content {
      name   = additional_configuration.key
      status = additional_configuration.value
    }
  }
}
