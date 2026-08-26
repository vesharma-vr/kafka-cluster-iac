output "instance_id" {
  description = "Instance ID of the Kafka broker. Connect via: aws ssm start-session --target <instance-id>"
  value       = aws_instance.kafka_broker.id
}

output "public_ip" {
  description = "Public IP of the Kafka broker instance (for external producer/consumer testing)."
  value       = aws_instance.kafka_broker.public_ip
}

output "private_ip" {
  description = "Private IP of the Kafka broker instance."
  value       = aws_instance.kafka_broker.private_ip
}

output "security_group_id" {
  description = "ID of the Kafka broker security group."
  value       = aws_security_group.kafka_broker.id
}
