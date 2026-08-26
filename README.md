# Apache Kafka on EC2 — CloudFormation vs. CDK vs. Terraform

A single self-managed Apache Kafka broker (KRaft mode — no Zookeeper, no MSK) deployed
three different ways, so the same infrastructure can be compared side by side across the
three major AWS IaC tools.

This is a learning-oriented example, not a production or HA design: one EC2 instance, one
broker, a wide-open security group, and no SSH key pair anywhere (access is via AWS SSM
Session Manager only). Each of the three implementations was deployed to a real AWS account,
verified end-to-end (create topic → produce → consume), and torn down.

## Layout

```
cloudformation/   kafka-simple.yaml + README.md
cdk/              Python CDK app (app.py, kafka_cdk/kafka_cluster_stack.py) + README.md
terraform/        main.tf / vpc-lookup / security / iam / ec2 / outputs + README.md
```

Each directory is self-contained with its own README covering exact deploy/verify/destroy
steps for that tool.

## What's the same across all three

- Kafka 4.3.1 (KRaft-only — Zookeeper mode was removed upstream in Kafka 4.x)
- Single `t3.small` EC2 instance, Amazon Linux 2023, 20 GiB gp3 root volume
- Combined broker+controller node (`process.roles=broker,controller`), it is its own and
  only quorum voter — a genuine single-node "cluster," not a stub
- Deployed into the AWS account's **existing default VPC** — none of the three creates a
  custom VPC, subnet, internet gateway, or route table
- One security group: TCP 9092 open to `0.0.0.0/0` (flagged in every implementation as
  wide-open for learning-example simplicity only — scope this to your own IP/CIDR for
  anything beyond a throwaway test), no SSH ingress at all
- One IAM role + instance profile granting only `AmazonSSMManagedInstanceCore` — the sole
  access path to the instance is `aws ssm start-session --target <instance-id>`, no SSH key
  pair exists anywhere in any of the three implementations
- Identical bootstrap logic on boot: install Corretto 17 → create a dedicated `kafka` OS
  user → download/extract Kafka to `/opt/kafka` → resolve the instance's own private IP via
  IMDSv2 → write `config/server.properties` → `kafka-storage.sh format` with a fixed cluster
  ID → register a systemd unit → `systemctl enable --now kafka`

## The one genuine difference between the tools

CloudFormation has no way to look up "the account's default VPC" at template-authoring time
— it has no live-AWS-account-lookup capability at all. So `cloudformation/kafka-simple.yaml`
takes `VpcId` and `SubnetId` as required `Parameters` that you look up yourself (via
`aws ec2 describe-vpcs` / `describe-subnets`) and pass in with `--parameter-overrides`.

CDK and Terraform can both query the account directly:
- CDK: `ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)` — a live lookup performed
  during `cdk synth`, which is why synthesizing this particular stack requires real AWS
  credentials (a from-scratch-VPC CDK stack wouldn't need that).
- Terraform: `data "aws_vpc" "default" { default = true }` plus a `data "aws_subnets"` filter
  — resolved during `plan`/`apply`.

This is a good illustration of a real practical tradeoff between the tools, not just a
styling difference.

## A bug worth knowing about (Kafka 4.x config layout)

Earlier Kafka versions (3.x) shipped a `config/kraft/server.properties` template alongside
the legacy `config/server.properties` (Zookeeper-mode) file, since 3.x supported both modes.
Kafka 4.x removed Zookeeper mode entirely and flattened the layout — there is no `config/kraft/`
subdirectory at all anymore, just `config/server.properties` directly. All three
implementations were originally written assuming the old `config/kraft/` path (carried over
from outdated documentation/tutorials still describing Kafka 3.x), which caused the bootstrap
script to fail (`No such file or directory`) on every actual deploy. This was caught during
live verification and fixed identically across all three (`cat > /opt/kafka/config/server.properties`,
no `kraft/` subdirectory) — a useful reminder to verify tutorial-derived paths against the
actual version you're deploying rather than trusting older docs.

## Verification performed

For each of the three implementations, in a real AWS account (us-east-1):
1. Deployed with the tool's native command (`cloudformation deploy` / `cdk deploy` /
   `terraform apply`)
2. Connected via `aws ssm start-session` (no SSH), confirmed `systemctl status kafka` showed
   `active (running)`
3. Created a topic, produced a message, consumed it back — confirmed real end-to-end
   functionality, not just "the process started"
4. Destroyed the stack (`cloudformation delete-stack` / `cdk destroy` / `terraform destroy`)
   and confirmed clean teardown with no leftover resources

## Cleanup

Each directory's README has the full step-by-step (including waiting for the Kafka bootstrap
and verifying via SSM before tearing down), but the actual delete command per tool is:

```bash
# CloudFormation
aws cloudformation delete-stack --stack-name kafka-simple-cfn --region us-east-1
aws cloudformation wait stack-delete-complete --stack-name kafka-simple-cfn --region us-east-1

# CDK
cd cdk && npx aws-cdk@2 destroy --force

# Terraform
cd terraform && terraform destroy
```

None of the three ever creates a VPC/subnet of its own (they all reuse the account's existing
default VPC), so destroying each stack removes everything it created — EC2 instance, root EBS
volume, security group, IAM role/instance profile — and leaves the default VPC/subnet
untouched.

**Confirm nothing is left running** (safe to run any time, not just right after a destroy):

```bash
# Any CFN/CDK stacks still around?
aws cloudformation list-stacks --region us-east-1 \
  --query "StackSummaries[?(contains(StackName,'kafka-simple') || contains(StackName,'KafkaSimple')) && StackStatus!='DELETE_COMPLETE'].[StackName,StackStatus]" \
  --output text

# Any EC2 instances still tagged from this project?
aws ec2 describe-instances --region us-east-1 \
  --filters "Name=tag:Project,Values=kafka-simple-no-msk" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text

# Terraform state empty?
cd terraform && python3 -c "import json; print(len(json.load(open('terraform.tfstate')).get('resources', [])))"
```

Empty output from the first two commands and `0` from the third means everything is torn
down. As of the last verification pass, all three stacks were confirmed fully deleted with no
leftover EC2 instances, security groups, or IAM roles.

## Cost

Each implementation costs roughly $0.02/hr while running (single `t3.small` + 20 GiB gp3,
reusing the account's existing default VPC — no NAT gateway, no custom networking spend).
None of the three stacks were left running after verification.
