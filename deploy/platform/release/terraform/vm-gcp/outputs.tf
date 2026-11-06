output "instance_id" { value = google_compute_instance.app.instance_id }
output "instance_name" { value = google_compute_instance.app.name }
output "private_ip" { value = google_compute_instance.app.network_interface[0].network_ip }
output "zone" { value = google_compute_instance.app.zone }
