# Preserve the deployed firewall and log delivery when adding optional instances.
moved {
  from = aws_networkfirewall_firewall.central
  to   = aws_networkfirewall_firewall.central[0]
}

moved {
  from = aws_networkfirewall_logging_configuration.central
  to   = aws_networkfirewall_logging_configuration.central[0]
}
