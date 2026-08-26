# kafka-simple-tf (Terraform)

Self-managed Apache Kafka 4.3.1 (KRaft mode, no Zookeeper, no MSK) on a single EC2 instance,
provisioned with Terraform. This is a learning-oriented example, not a production or HA
design: one instance, one broker, one KRaft quorum voter.

Access to the instance is via AWS Systems Manager Session Manager only — no SSH key pair is
created or referenced anywhere in this stack.

## What this configuration creates

- 1 security group: ingress TCP 9092 from `0.0.0.0/0` for learning-example simplicity — scope
  this to a specific CIDR (e.g. your own IP) for anything beyond a throwaway test — no SSH
  ingress rule, allow-all egress
- 1 IAM role (trust: `ec2.amazonaws.com`) with `AmazonSSMManagedInstanceCore` attached,
  wrapped in an instance profile
- 1 `t3.small` EC2 instance, latest Amazon Linux 2023 x86_64 (resolved via the public SSM
  parameter `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64`, never
  hardcoded), 20 GiB gp3 root volume, public IP

**Why this doesn't create a VPC:** unlike a from-scratch design, this stack does not create a
VPC, subnets, an internet gateway, or route tables. It uses the AWS account's existing
**default VPC** and one of its default subnets, resolved at plan/apply time via data sources:

```hcl
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}
```

The first subnet returned (`element(data.aws_subnets.default.ids, 0)`) is used for the
instance. This is a live lookup against the AWS account, so real credentials are required for
`plan`/`apply` (but not for `validate`). This is the most direct of the three
implementations' approaches to "use the default VPC" — CloudFormation has to take
`VpcId`/`SubnetId` as input parameters instead, since it has no live-lookup capability at
template-authoring time; CDK uses `ec2.Vpc.from_lookup(is_default=True)`.

## Prerequisites

- Terraform >= 1.5.0
- AWS credentials configured (e.g. `aws configure`, environment variables, or an SSO profile)
  with permissions to create EC2/IAM/SSM resources and read the default VPC/subnets, in
  `us-east-1`

## Step 1 — Initialize

```bash
cd terraform
terraform init
```

Downloads the AWS provider plugin.

## Step 2 — Validate

```bash
terraform validate
```

Should print `Success! The configuration is valid.` — this doesn't require live credentials.

## Step 3 — Plan

```bash
terraform plan -out=tfplan
```

This resolves the real default VPC/subnet/AMI via the data sources above (so it does need
live credentials) and shows exactly what will be created — expect `Plan: 7 to add, 0 to
change, 0 to destroy` (security group, 2 SG rules, IAM role, policy attachment, instance
profile, EC2 instance). Review it before proceeding.

## Step 4 — Apply

```bash
terraform apply tfplan
```

Takes roughly 15-30 seconds for Terraform itself to finish creating resources. On success it
prints `instance_id`, `public_ip`, `private_ip`, and `security_group_id`.

## Step 5 — Wait for the Kafka bootstrap to finish

`apply` finishing only means the EC2 instance was launched — `user_data.sh` (install Java,
download Kafka, format storage, start the systemd service) still needs to run on first boot.
Poll for the SSM agent to register:

```bash
INSTANCE_ID=$(terraform output -raw instance_id)

aws ssm describe-instance-information --region us-east-1 \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
  --query 'InstanceInformationList[0].PingStatus' --output text
```

Wait until this prints `Online`, then give it another ~30-60 seconds for the bootstrap script
itself to finish.

## Step 6 — Connect via SSM (no SSH key needed)

```bash
aws ssm start-session --target $INSTANCE_ID
```

## Step 7 — Confirm Kafka is running

On the instance:

```bash
sudo systemctl status kafka
```

Expect `Active: active (running)`. If it isn't, check `sudo journalctl -u kafka -n 50` and
`cat /var/log/cloud-init-output.log` for bootstrap errors.

## Step 8 — Create a topic, produce, and consume a test message

Still on the instance:

```bash
sudo /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 \
  --create --topic verify-topic --partitions 1 --replication-factor 1

sudo /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 \
  --describe --topic verify-topic

echo "hello kafka" | sudo /opt/kafka/bin/kafka-console-producer.sh \
  --bootstrap-server localhost:9092 --topic verify-topic

sudo /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 \
  --topic verify-topic --from-beginning --timeout-ms 5000
```

Expected: the `--describe` shows `PartitionCount: 1 ReplicationFactor: 1` with `Leader: 1`,
and the consumer prints `hello kafka` before it times out. This has been run end-to-end
against a real deployment and confirmed working.

## Step 9 — Clean up

Exit the SSM session (`exit`), then from your local machine:

```bash
terraform destroy
```

Confirm with `yes` when prompted. This removes the EC2 instance, security group, IAM role,
policy attachment, and instance profile. It does NOT touch the account's default VPC or its
subnets — they were only ever read via data sources, never owned by this configuration.

**Confirm the cleanup actually completed:**

```bash
terraform state list
```

Should print nothing (empty state). Also double-check no EC2 instance from this stack is
still running:

```bash
aws ec2 describe-instances --region us-east-1 \
  --filters "Name=tag:Project,Values=kafka-simple-no-msk" "Name=tag:Iac,Values=tf" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text
```

Output should show no instances, or only instances with state `terminated` — AWS keeps
terminated instances visible via `describe-instances` for a while before they age out, so
`terminated` (not just empty output) also confirms the stack is fully torn down.

## Notes

- No remote backend is configured; state is stored locally (`terraform.tfstate`). Configure a
  remote backend (e.g. S3 + DynamoDB lock table) before using this in a shared/production
  context.
- `user_data.sh` is a plain static script (not a `.tftpl` template): since there is only one
  instance and no per-node values, nothing needs substitution — it's read as-is via
  `file("${path.module}/user_data.sh")`.
- The single node uses the fixed KRaft cluster ID `zsvUnJ-LSWW-5vb3PEALHQ` and is its own and
  only quorum voter (`process.roles=broker,controller`). It resolves its own private IP at
  boot via the IMDSv2 metadata service rather than a static/hardcoded address.

## Known gotcha (already fixed in this configuration, worth knowing)

Kafka 3.x shipped a `config/kraft/server.properties` template alongside the legacy
`config/server.properties` (Zookeeper-mode) file. Kafka 4.x removed Zookeeper mode entirely
and flattened the layout — there's no `config/kraft/` subdirectory anymore, just
`config/server.properties` directly. If you're adapting `user_data.sh` for a different Kafka
version, double-check that path against the actual tarball contents rather than trusting
older docs/tutorials.
