# kafka-simple-cfn (CloudFormation)

Self-managed Apache Kafka **4.3.1** running on a **single** EC2 instance, in **KRaft mode**
(no Zookeeper, no AWS MSK), inside the account's **default VPC**. This is a learning-oriented
example, not a production or HA design: one instance, one broker acting as its own (and only)
KRaft quorum voter, a wide-open security group, and no SSH key pair anywhere.

There is **no SSH key pair** in this stack. The only way to reach the instance is via AWS
Systems Manager Session Manager (SSM), using the IAM instance profile attached to it.

## What this template creates

- 1 security group: ingress TCP 9092 (Kafka client/data plane) from `0.0.0.0/0` — wide open
  for learning-example simplicity, no SSH ingress rule at all — and allow-all egress
- 1 IAM role (trust: `ec2.amazonaws.com`) with `AmazonSSMManagedInstanceCore` attached,
  wrapped in an instance profile
- 1 `t3.small` EC2 instance (latest Amazon Linux 2023 x86_64, resolved at deploy time via
  the public SSM parameter `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64`,
  never hardcoded), with a 20 GiB `gp3` root volume and a public IP

The instance's `UserData` installs Corretto 17, downloads and installs Kafka 4.3.1 under
`/opt/kafka`, writes `/opt/kafka/config/server.properties` (resolving its own private IP at
boot via IMDSv2 for `advertised.listeners`/`controller.quorum.voters`), formats storage with
the fixed cluster ID `zsvUnJ-LSWW-5vb3PEALHQ`, and runs Kafka as a systemd service
(`kafka.service`) under a dedicated `kafka` OS user. Since there is exactly one instance and
nothing to parameterize per-node, the template uses a plain `Fn::Base64` around a literal
multi-line string for `UserData` — no `Fn::Sub` anywhere, because there is nothing to
substitute.

All resources are tagged `Project=kafka-simple-no-msk`, `Iac=cfn`.

**Why `VpcId`/`SubnetId` are input parameters instead of being looked up automatically:**
unlike CDK (`ec2.Vpc.from_lookup(is_default=True)`) or Terraform (`data "aws_vpc" "default"`),
CloudFormation has no live-AWS-account-lookup capability at template-authoring time — a CFN
template is a static document, and it can't query your account for "the default VPC" the way
a CDK synth or Terraform plan can. So you resolve those two values yourself first (Step 2
below) and pass them in as parameters.

## Prerequisites

- AWS CLI v2, configured with credentials that can create EC2/IAM/SSM resources
- An account with a default VPC (nearly all accounts have one unless it was explicitly deleted)
- Target region: `us-east-1`

## Step 1 — Validate the template (optional but recommended)

```bash
cd cloudformation
cfn-lint kafka-simple.yaml
```

No output means it passed.

## Step 2 — Find your default VPC and a subnet in it

```bash
# Find the default VPC id
aws ec2 describe-vpcs \
  --filters Name=is-default,Values=true \
  --region us-east-1 \
  --query "Vpcs[0].VpcId" \
  --output text

# Find subnets in that VPC (pick any one -- default-VPC subnets auto-assign public IPs)
aws ec2 describe-subnets \
  --filters Name=vpc-id,Values=<vpc-id-from-above> \
  --region us-east-1 \
  --query "Subnets[].{SubnetId:SubnetId,AZ:AvailabilityZone}" \
  --output table
```

Keep the `VpcId` and one `SubnetId` from the output — you'll pass both into the deploy command.

## Step 3 — Deploy the stack

```bash
aws cloudformation deploy \
  --template-file kafka-simple.yaml \
  --stack-name kafka-simple-cfn \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides VpcId=<vpc-id> SubnetId=<subnet-id> \
  --region us-east-1
```

`CAPABILITY_NAMED_IAM` is required because the template creates a named IAM role
(`kafka-simple-cfn-instance-role`) and instance profile. This takes roughly 1-2 minutes for
CloudFormation itself to finish creating resources.

## Step 4 — Wait for the Kafka bootstrap to finish

The stack reaching `CREATE_COMPLETE` only means the EC2 instance was launched — the
`UserData` script (install Java, download Kafka, format storage, start the systemd service)
still needs to run on first boot. Give it 2-3 minutes, or poll for the SSM agent to register:

```bash
INSTANCE_ID=$(aws cloudformation describe-stacks --stack-name kafka-simple-cfn --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)

aws ssm describe-instance-information --region us-east-1 \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
  --query 'InstanceInformationList[0].PingStatus' --output text
```

Wait until this prints `Online`.

## Step 5 — Get the stack outputs

```bash
aws cloudformation describe-stacks \
  --stack-name kafka-simple-cfn \
  --region us-east-1 \
  --query "Stacks[0].Outputs" \
  --output table
```

You'll get `InstanceId`, `PublicIp`, `PrivateIp`, and `SecurityGroupId`.

## Step 6 — Connect via SSM (no SSH key needed)

```bash
aws ssm start-session --target <InstanceId> --region us-east-1
```

This drops you into a shell on the instance.

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
aws cloudformation delete-stack --stack-name kafka-simple-cfn --region us-east-1
aws cloudformation wait stack-delete-complete --stack-name kafka-simple-cfn --region us-east-1
```

This removes everything the stack created (security group, IAM role/instance profile, EC2
instance and its root EBS volume — `DeleteOnTermination: true` is the default). The default
VPC and subnet themselves are never touched; this stack only ever reads their ids via the
parameters you supplied.

**Confirm the cleanup actually completed:**

```bash
aws cloudformation describe-stacks --stack-name kafka-simple-cfn --region us-east-1 \
  --query 'Stacks[0].StackStatus' --output text
```

This should either error with `Stack ... does not exist` or, if you look it up by name via
`list-stacks` instead, show `DELETE_COMPLETE`:

```bash
aws cloudformation list-stacks --region us-east-1 \
  --query "StackSummaries[?StackName=='kafka-simple-cfn'].StackStatus" --output text
```

And double-check no EC2 instance from this stack is still running:

```bash
aws ec2 describe-instances --region us-east-1 \
  --filters "Name=tag:Project,Values=kafka-simple-no-msk" "Name=tag:Iac,Values=cfn" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text
```

Empty output means it's fully torn down.

## Known gotcha (already fixed in this template, worth knowing)

Kafka 3.x shipped a `config/kraft/server.properties` template alongside the legacy
`config/server.properties` (Zookeeper-mode) file. Kafka 4.x removed Zookeeper mode entirely
and flattened the layout — there's no `config/kraft/` subdirectory anymore, just
`config/server.properties` directly. If you're adapting this template for a different Kafka
version, double-check that path against the actual tarball contents rather than trusting
older docs/tutorials.
