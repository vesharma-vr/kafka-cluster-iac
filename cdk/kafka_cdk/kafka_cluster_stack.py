"""
KafkaSimpleCdkStack

The smallest, most readable "Apache Kafka on EC2, not MSK" example -- optimized
for a learner reading the code top-to-bottom, not for HA or production security
posture. One EC2 instance, one Kafka broker, KRaft mode (no Zookeeper).

- Uses the AWS account's existing DEFAULT VPC (via a live `ec2.Vpc.from_lookup`
  lookup at synth time, cached into cdk.context.json) -- no custom VPC, subnet,
  internet gateway, or route table is created. This is the #1 simplification
  vs. a from-scratch-VPC design, and it's also the one genuine difference
  worth calling out: unlike CloudFormation (which has no live-account-lookup
  capability at template-authoring time and must take VpcId/SubnetId as
  Parameters instead), CDK can just ask the account "what's my default VPC"
  right here, which is why `cdk synth` for this stack needs real AWS
  credentials.
- 1 security group: ingress TCP 9092 from 0.0.0.0/0 (wide open -- fine for a
  disposable learning example, but scope this to a specific CIDR, e.g. your
  own IP, before using this for anything beyond a throwaway test), no SSH
  ingress, egress all.
- 1 IAM role (AmazonSSMManagedInstanceCore) + instance profile -- SSM is the
  only access path, no SSH key pair anywhere in this stack.
- 1 t3.small EC2 instance, single Kafka broker running in combined
  broker+controller KRaft mode (it is its own and only quorum voter).
"""

from aws_cdk import (
    CfnOutput,
    Stack,
    Tags,
    aws_ec2 as ec2,
    aws_iam as iam,
)
from constructs import Construct

# ---------------------------------------------------------------------------
# Fixed spec values -- identical across all three IaC implementations.
# ---------------------------------------------------------------------------
NAME_PREFIX = "kafka-simple-cdk"

KAFKA_VERSION = "4.3.1"
KAFKA_SCALA_ARTIFACT = f"kafka_2.13-{KAFKA_VERSION}"
KAFKA_DOWNLOAD_URL = f"https://downloads.apache.org/kafka/{KAFKA_VERSION}/{KAFKA_SCALA_ARTIFACT}.tgz"
KRAFT_CLUSTER_ID = "zsvUnJ-LSWW-5vb3PEALHQ"

AL2023_AMI_SSM_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"

SSM_CONNECT_NOTE = (
    "Connect via SSM: aws ssm start-session --target <instance-id> "
    "(no SSH key pair exists in this design)"
)

# Single-node bootstrap script -- every value is a literal since there's only
# one node (no NODE_ID / controller-quorum-voters-list parameterization to
# worry about, unlike a multi-broker design). The only thing resolved at boot
# rather than baked in here is the instance's own private IP (via IMDSv2),
# since that isn't known until the instance actually launches.
USER_DATA = f"""#!/bin/bash
set -euxo pipefail

# 1. Packages
dnf install -y java-17-amazon-corretto-headless wget tar

# 2. Dedicated unprivileged OS user
if ! id kafka &>/dev/null; then
  useradd -r -m -d /home/kafka kafka
fi

# 3. Data dir
mkdir -p /var/lib/kafka/data
chown -R kafka:kafka /var/lib/kafka/data

# 4. Download + extract Kafka {KAFKA_VERSION} to /opt/kafka
cd /opt
wget -q "{KAFKA_DOWNLOAD_URL}"
tar -xzf "{KAFKA_SCALA_ARTIFACT}.tgz"
rm -f "{KAFKA_SCALA_ARTIFACT}.tgz"
rm -rf /opt/kafka
mv "/opt/{KAFKA_SCALA_ARTIFACT}" /opt/kafka
chown -R kafka:kafka /opt/kafka

# 5. Resolve this instance's own private IP live via IMDSv2 (single node, but
#    still not hardcoded -- it looks up its own address dynamically like
#    everything else)
TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
OWN_PRIVATE_IP=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4)

# 6. Write server.properties -- single-node KRaft, combined broker+controller,
#    this node is its own and only quorum voter
cat > /opt/kafka/config/server.properties <<EOF
process.roles=broker,controller
node.id=1
controller.quorum.voters=1@$OWN_PRIVATE_IP:9093
listeners=PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
inter.broker.listener.name=PLAINTEXT
advertised.listeners=PLAINTEXT://$OWN_PRIVATE_IP:9092
controller.listener.names=CONTROLLER
listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
log.dirs=/var/lib/kafka/data
num.partitions=1
default.replication.factor=1
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
EOF
chown kafka:kafka /opt/kafka/config/server.properties

# 7. Format storage (idempotent across reboots)
sudo -u kafka /opt/kafka/bin/kafka-storage.sh format \\
  --cluster-id {KRAFT_CLUSTER_ID} \\
  --config /opt/kafka/config/server.properties \\
  --ignore-formatted

# 8. systemd unit
cat > /etc/systemd/system/kafka.service <<'UNIT'
[Unit]
Description=Apache Kafka (KRaft, single node)
After=network.target

[Service]
Type=simple
User=kafka
Environment=JAVA_HOME=/usr/lib/jvm/java-17-amazon-corretto
ExecStart=/opt/kafka/bin/kafka-server-start.sh /opt/kafka/config/server.properties
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT

# 9. Enable + start
systemctl daemon-reload
systemctl enable --now kafka
"""


class KafkaSimpleCdkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Common tags on every taggable resource in this stack.
        Tags.of(self).add("Project", "kafka-simple-no-msk")
        Tags.of(self).add("Iac", "cdk")

        # -------------------------------------------------------------
        # Networking -- the account's existing DEFAULT VPC, looked up live.
        # No custom VPC/subnet/IGW/route table is created anywhere.
        # -------------------------------------------------------------
        vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        # Pick the first public subnet for the instance.
        subnet = vpc.public_subnets[0]

        # -------------------------------------------------------------
        # Security group -- ingress 9092 from anywhere, no SSH
        # -------------------------------------------------------------
        sg = ec2.SecurityGroup(
            self, "KafkaBrokerSg",
            vpc=vpc,
            description="Kafka client/data-plane traffic for the kafka-simple learning example",
            allow_all_outbound=True,
            security_group_name=f"{NAME_PREFIX}-sg",
        )
        Tags.of(sg).add("Name", f"{NAME_PREFIX}-sg")

        sg.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(9092),
            # Wide open (0.0.0.0/0) for learning-example simplicity. Scope this
            # to a specific CIDR (e.g. your own IP) for anything beyond a
            # throwaway test.
            description=(
                "Kafka client/data plane -- wide open for learning-example "
                "simplicity; scope to a specific CIDR (e.g. your own IP) for "
                "anything beyond a throwaway test"
            ),
        )
        # No SSH ingress rule -- access is via SSM Session Manager only.

        # -------------------------------------------------------------
        # IAM role + instance profile -- SSM only, no SSH
        # -------------------------------------------------------------
        role = iam.Role(
            self, "KafkaEc2Role",
            role_name=f"{NAME_PREFIX}-ec2-role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ],
        )
        instance_profile = iam.InstanceProfile(
            self, "KafkaInstanceProfile",
            instance_profile_name=f"{NAME_PREFIX}-instance-profile",
            role=role,
        )

        # -------------------------------------------------------------
        # AMI -- latest AL2023 x86_64 resolved via SSM dynamic reference
        # (resolved at deploy time by CloudFormation, not at synth time)
        # -------------------------------------------------------------
        machine_image = ec2.MachineImage.from_ssm_parameter(
            AL2023_AMI_SSM_PARAM,
            os=ec2.OperatingSystemType.LINUX,
        )

        # -------------------------------------------------------------
        # Compute -- exactly 1 t3.small instance, single Kafka broker
        # -------------------------------------------------------------
        instance = ec2.Instance(
            self, "KafkaBroker",
            instance_type=ec2.InstanceType("t3.small"),
            machine_image=machine_image,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=[subnet]),
            associate_public_ip_address=True,
            security_group=sg,
            instance_profile=instance_profile,
            instance_name="kafka-simple-broker",
            user_data=ec2.UserData.custom(USER_DATA),
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        20,
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                        delete_on_termination=True,
                    ),
                ),
            ],
        )

        # -------------------------------------------------------------
        # Outputs
        # -------------------------------------------------------------
        CfnOutput(
            self, "InstanceId",
            value=instance.instance_id,
            description=f"kafka-simple-broker instance ID -- {SSM_CONNECT_NOTE}",
        )
        CfnOutput(
            self, "PublicIp",
            value=instance.instance_public_ip,
            description="kafka-simple-broker public IP (for external producer/consumer testing)",
        )
        CfnOutput(
            self, "PrivateIp",
            value=instance.instance_private_ip,
            description="kafka-simple-broker private IP",
        )
        CfnOutput(
            self, "SecurityGroupId",
            value=sg.security_group_id,
            description=f"{NAME_PREFIX}-sg security group ID",
        )
