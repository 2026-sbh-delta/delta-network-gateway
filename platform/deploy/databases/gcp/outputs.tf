output "instance_name" { value = google_sql_database_instance.app.name }
output "connection_name" { value = google_sql_database_instance.app.connection_name }
output "host" { value = google_sql_database_instance.app.dns_name }
output "private_ip" { value = google_compute_address.database.address }
output "admin_secret_id" { value = google_secret_manager_secret.admin.id }
output "server_ca_pem" { value = var.ca_pem }
