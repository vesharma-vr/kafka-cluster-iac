resource "aws_security_group" "kafka_broker" {
  name        = "${var.name_prefix}-sg"
  description = "Kafka single-node SG: Kafka client/data-plane ingress, no SSH"
  vpc_id      = data.aws_vpc.default.id

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-sg"
  })
}

# Wide-open on purpose for learning-example simplicity: this lets any external
# producer/consumer reach the single broker without extra networking setup.
# For anything beyond a throwaway test, scope this down to a specific CIDR
# (e.g. your own IP/32) instead of 0.0.0.0/0.
resource "aws_security_group_rule" "kafka_ingress_9092" {
  type              = "ingress"
  from_port         = 9092
  to_port           = 9092
  protocol          = "tcp"
  security_group_id = aws_security_group.kafka_broker.id
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Kafka client/data-plane traffic from anywhere (learning-example simplicity; scope to your own IP for anything beyond a throwaway test)"
}

# No SSH ingress rule: access is via SSM Session Manager only, no key pair anywhere.

resource "aws_security_group_rule" "kafka_egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.kafka_broker.id
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Allow all outbound (package installs, Kafka tarball download, SSM)"
}
