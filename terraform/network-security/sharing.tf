locals {
  shared_resources = merge(
    { tgw = aws_ec2_transit_gateway.central.arn },
    local.firewall_enabled ? { firewall = aws_networkfirewall_firewall.central[0].arn } : {}
  )
  share_principals = { for item in flatten([for kind in ["tgw", "firewall"] : [for account_id in var.shared_account_ids : {
    key = "${kind}/${account_id}", kind = kind, account_id = account_id
  }]]) : item.key => item }
}

resource "aws_ram_resource_share" "central" {
  for_each                  = toset(["tgw", "firewall"])
  name                      = "${local.prefix}-${each.key}"
  allow_external_principals = var.allow_external_principals
  tags                      = { Name = "${local.prefix}-${each.key}-share" }
  depends_on                = [terraform_data.contract]
}

resource "aws_ram_resource_association" "central" {
  for_each           = local.shared_resources
  resource_arn       = each.value
  resource_share_arn = aws_ram_resource_share.central[each.key].arn
}

resource "aws_ram_principal_association" "consumer" {
  for_each           = local.share_principals
  principal          = each.value.account_id
  resource_share_arn = aws_ram_resource_share.central[each.value.kind].arn
}
