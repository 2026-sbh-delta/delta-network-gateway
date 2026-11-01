resource "aws_networkfirewall_rule_group" "central" {
  name     = "${local.prefix}-stateful"
  capacity = var.stateful_rule_capacity
  type     = "STATEFUL"

  rule_group {
    stateful_rule_options {
      rule_order = "STRICT_ORDER"
    }
    rule_variables {
      ip_sets {
        key = "HOME_NET"
        ip_set {
          definition = local.home_net
        }
      }
      ip_sets {
        key = "EXTERNAL_NET"
        ip_set {
          # Rule matching only; NOT an internet route. Includes HOME_NET peers.
          definition = ["0.0.0.0/0"]
        }
      }
    }
    rules_source {
      rules_string = local.rules_string
    }
  }
  tags       = { Name = "${local.prefix}-stateful" }
  depends_on = [terraform_data.contract]
}

resource "aws_networkfirewall_firewall_policy" "central" {
  name = "${local.prefix}-policy"
  firewall_policy {
    stateless_default_actions          = ["aws:forward_to_sfe"]
    stateless_fragment_default_actions = ["aws:forward_to_sfe"]

    # No built-in drop: it would also drop flows marked by nonterminating alert
    # rules. The last explicit flowbits:isnotset rule supplies default deny.
    stateful_engine_options {
      rule_order              = "STRICT_ORDER"
      stream_exception_policy = "DROP"
    }
    policy_variables {
      rule_variables {
        key = "HOME_NET"
        ip_set {
          definition = local.home_net
        }
      }
    }
    stateful_rule_group_reference {
      priority     = 100
      resource_arn = aws_networkfirewall_rule_group.central.arn
    }
  }
  tags = { Name = "${local.prefix}-policy" }
}
