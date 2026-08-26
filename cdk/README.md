# kafka-simple-cdk

Python CDK (aws-cdk-lib v2) app that deploys the smallest, most readable "Apache Kafka on
EC2, not MSK" example: **one EC2 instance, one Kafka broker, KRaft mode (no Zookeeper)**, in
the AWS account's existing **default VPC** (no custom VPC/subnet/internet gateway/route table
is created).

Stack id: `KafkaSimpleCdkStack`.

There is **no SSH key pair** in this stack. The only way to reach the instance is via AWS
Systems Manager Session Manager (SSM), using the IAM instance profile attached to it.

## What this stack creates

- 1 security group allowing ingress on TCP 9092 from `0.0.0.0/0` (wide open for
  learning-example simplicity — scope this to a specific CIDR, e.g. your own IP, for anything
  beyond a throwaway test) and no SSH ingress rule
- 1 IAM role (trusted by `ec2.amazonaws.com`) with `AmazonSSMManagedInstanceCore` attached,
  wrapped in an instance profile
- 1 `t3.small` EC2 instance (`kafka-simple-broker`) with a 20 GiB gp3 root volume and a public
  IP, bootstrapping Apache Kafka 4.3.1 in single-node KRaft mode via user data (cluster id
  `zsvUnJ-LSWW-5vb3PEALHQ`, combined broker+controller, its own and only quorum voter)

**Why this stack needs live AWS credentials just to synthesize:** unlike a from-scratch-VPC
stack, this one performs a live lookup of the account's default VPC
(`ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)`) at synth time — CDK actually
queries your account before generating the CloudFormation template. CloudFormation itself
can't do this (see the `cloudformation/` README for why it needs explicit `VpcId`/`SubnetId`
parameters instead), and this is the one genuine mechanical difference between the three
implementations, not just a styling choice.

## Prerequisites

- Python 3.9+
- Node.js (required by the AWS CDK Toolkit / jsii runtime)
- AWS credentials configured for `us-east-1` (e.g. `export AWS_PROFILE=<your-profile>`)

## Step 1 — Set up the Python environment

```bash
cd cdk
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate.bat
pip install -r requirements.txt
```

## Step 2 — Bootstrap CDK (first time only, per account/region)

Skip this if the account/region has already been CDK-bootstrapped (check for a
`CDKToolkit` CloudFormation stack):

```bash
aws cloudformation describe-stacks --stack-name CDKToolkit --region us-east-1 \
  --query 'Stacks[0].StackStatus' --output text
```

If that errors (stack not found), bootstrap:

```bash
npx aws-cdk@2 bootstrap aws://<ACCOUNT_ID>/us-east-1
```

## Step 3 — Synthesize (requires real AWS credentials)

```bash
npx aws-cdk@2 synth
```

This performs the live default-VPC lookup, renders the CloudFormation template to `cdk.out/`,
and is a good sanity check before deploying. The lookup result is cached into
`cdk.context.json`, which is intentionally committed so subsequent synths/deploys are
reproducible without re-querying the account — delete it and re-run `synth` if the account's
default VPC configuration ever changes.

## Step 4 — Deploy

```bash
npx aws-cdk@2 deploy --require-approval never
```

Deployment takes roughly 3 minutes (IAM role/instance profile creation has an inherent
propagation delay before the EC2 instance can launch with it attached). On success, the
stack prints its outputs: instance ID, public IP, private IP, and security group ID.

## Step 5 — Wait for the Kafka bootstrap to finish

`cdk deploy` finishing only means the EC2 instance was launched — the user-data script
(install Java, download Kafka, format storage, start the systemd service) still needs to run
on first boot. Poll for the SSM agent to register:

```bash
INSTANCE_ID=<InstanceId from the deploy output>

aws ssm describe-instance-information --region us-east-1 \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
  --query 'InstanceInformationList[0].PingStatus' --output text
```

Wait until this prints `Online`, then give it another ~30-60 seconds for the bootstrap script
itself to finish.

## Step 6 — Connect via SSM (no SSH key needed)

```bash
aws ssm start-session --target <instance-id>
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
npx aws-cdk@2 destroy --force
```

This tears down the EC2 instance, security group, and IAM role/instance profile created by
this stack. It does NOT touch the account's default VPC or its subnets — they were only ever
looked up, never owned by this stack.

**Confirm the cleanup actually completed:**

```bash
aws cloudformation list-stacks --region us-east-1 \
  --query "StackSummaries[?StackName=='KafkaSimpleCdkStack'].StackStatus" --output text
```

Should show `DELETE_COMPLETE` (or nothing at all once it ages out of the list). Also
double-check no EC2 instance from this stack is still running:

```bash
aws ec2 describe-instances --region us-east-1 \
  --filters "Name=tag:Project,Values=kafka-simple-no-msk" "Name=tag:Iac,Values=cdk" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text
```

Empty output means it's fully torn down.

Optionally clean up the local Python environment and synth output:

```bash
deactivate
rm -rf .venv cdk.out
```

(Keep `cdk.context.json` — it's meant to be committed.)

## Known gotcha (already fixed in this stack, worth knowing)

Kafka 3.x shipped a `config/kraft/server.properties` template alongside the legacy
`config/server.properties` (Zookeeper-mode) file. Kafka 4.x removed Zookeeper mode entirely
and flattened the layout — there's no `config/kraft/` subdirectory anymore, just
`config/server.properties` directly. If you're adapting this stack for a different Kafka
version, double-check that path against the actual tarball contents rather than trusting
older docs/tutorials.
