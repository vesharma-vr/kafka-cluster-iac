# The account's default VPC and one of its default subnets — no custom VPC,
# subnets, internet gateway, or route tables are created for this design.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "kafka_broker" {
  ami                         = data.aws_ssm_parameter.al2023_ami.value
  instance_type               = "t3.small"
  subnet_id                   = element(data.aws_subnets.default.ids, 0)
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.kafka_broker.id]
  iam_instance_profile        = aws_iam_instance_profile.kafka_broker.name

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  # Plain static script — a single instance with no per-node values means
  # there is nothing to template/substitute.
  user_data = file("${path.module}/user_data.sh")

  tags = merge(local.common_tags, {
    Name = "kafka-simple-broker"
  })
}
