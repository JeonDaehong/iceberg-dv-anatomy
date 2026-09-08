#!/usr/bin/env bash
# EC2 인스턴스를 띄워 bootstrap.sh 를 돌린다.
#
# 왜 이 파일이 뒤늦게 생겼나:
#   F-020·F-021·F-022 는 EC2 에서 나왔는데, 저장소에는 `bootstrap.sh`(인스턴스 **안**에서
#   도는 것)만 있고 **띄우는 명령**이 없었다. 그래서 "재현: cloud/bootstrap.sh" 라고
#   적어놓고 정작 그 앞 단계는 내 셸 히스토리에만 있었다.
#   이 저장소가 F-021 에서 스스로 지적한 문제("문서에 재현 명령을 적어놓고 실제로는
#   다른 걸 돌렸다")와 같은 종류다. 그래서 명령을 파일로 옮긴다.
#
# 안전:
#   - user-data 가 부팅 직후 `shutdown -h +240` 을 걸고, 인스턴스는
#     instance-initiated-shutdown-behavior=terminate 로 뜬다.
#     => 이 스크립트가 죽든 세션이 끊기든 4시간 뒤 스스로 사라진다.
#   - 태그 Name=dv-anatomy-<MODE>-<타임스탬프> 로 뜨므로 나중에 찾기 쉽다.
#
# 사용:
#   MODE=s3many ./launch.sh
#   MODE=shuffle TYPE=m7i.xlarge ./launch.sh
#   MODE=cpu TYPE=m7g.xlarge ./launch.sh          # arm64 는 AMI 도 arm64 로 잡힌다
set -euo pipefail

# ⚠️ Git Bash(MSYS)는 `/` 로 시작하는 인자를 Windows 경로로 바꿔버린다.
#    그래서 --block-device-mappings 의 `/dev/sda1` 이
#    `C:/Program Files/Git/dev/sda1` 이 되어 InvalidBlockDeviceMapping 으로 죽었다.
#    리눅스에서는 이 변수가 아무 일도 안 하므로 그냥 켜둔다.
export MSYS_NO_PATHCONV=1

MODE="${MODE:-cpu}"
TYPE="${TYPE:-m7i.xlarge}"
REGION="${AWS_REGION:-ap-northeast-2}"
BUCKET="${BUCKET:-dv-anatomy-394686422456-apne2}"
DISK_GB="${DISK_GB:-60}"
# 파일 수 축은 32M행 테이블 두 개(약 6.6GB)를 만든다. 기본 60GB 면 충분하지만
# 재실행으로 쌓이면 모자랄 수 있어 모드별로 올린다.
[[ "$MODE" == "s3many" ]] && DISK_GB="${DISK_GB_S3MANY:-100}"

TS="$(date -u +%m%d-%H%M)"
TAG="${DV_TAG:-${MODE}-${TYPE%%.*}-${TS}}"
NAME="dv-anatomy-${TAG}"

command -v aws >/dev/null || { echo "aws CLI 가 없다" >&2; exit 1; }
aws sts get-caller-identity --region "$REGION" >/dev/null \
  || { echo "AWS 인증이 안 돼 있다. 'aws login' 을 먼저 하라." >&2; exit 1; }

# 아키텍처는 인스턴스 타입에서 유도한다. g 계열(m7g, c7g...) 이 Graviton 이다.
case "$TYPE" in
  *g.*|*gd.*) ARCH=arm64 ;;
  *)          ARCH=amd64 ;;
esac

# AMI 는 Canonical 공식 계정(099720109477)에서 최신 24.04 를 직접 고른다.
# ⚠️ 처음엔 SSM 별칭(/aws/service/canonical/...)을 썼는데 ParameterNotFound 로 죽었다 —
#    Canonical 은 리전·배포마다 그 별칭 경로를 바꾼다. describe-images 는 안 바뀐다.
AMI="${AMI:-}"
if [[ -z "$AMI" ]]; then
  AMI=$(aws ec2 describe-images --region "$REGION" --owners 099720109477 \
    --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-${ARCH}-server-*" \
              "Name=state,Values=available" \
    --query 'reverse(sort_by(Images,&CreationDate))[0].ImageId' --output text)
fi
[[ -n "$AMI" && "$AMI" != "None" ]] || { echo "AMI 조회 실패 (arch=${ARCH})" >&2; exit 1; }

# 인스턴스 프로파일: SSM + 이 버킷에 대한 S3 읽기/쓰기.
#
# ⚠️ 이름으로 'dv' 를 찾게 해뒀다가 못 찾았다 — 이 계정의 프로파일 이름은
#    EC2-SSM-Profile 이다. 이름 규칙에 기대지 말고 계정에 하나뿐이면 그걸 쓴다.
#
# ⚠️ 그리고 그 역할에는 **S3 권한이 기본으로 없다.** 붙이지 않으면 인스턴스가
#    jar 다운로드 단계에서 죽는다. 한 번만 실행하면 된다 (버킷 하나로 제한된 정책):
#      aws iam put-role-policy --role-name EC2-SSM-Role \
#        --policy-name dv-anatomy-s3 \
#        --policy-document file://cloud/iam-dv-anatomy-s3.json
#    다 쓰고 나면:
#      aws iam delete-role-policy --role-name EC2-SSM-Role --policy-name dv-anatomy-s3
PROFILE="${IAM_PROFILE:-}"
if [[ -z "$PROFILE" ]]; then
  PROFILE=$(aws iam list-instance-profiles \
    --query "InstanceProfiles[?contains(InstanceProfileName, 'dv')].InstanceProfileName | [0]" \
    --output text 2>/dev/null || true)
fi
if [[ -z "$PROFILE" || "$PROFILE" == "None" ]]; then
  PROFILE=$(aws iam list-instance-profiles \
    --query "InstanceProfiles[0].InstanceProfileName" --output text 2>/dev/null || true)
fi
[[ -n "$PROFILE" && "$PROFILE" != "None" ]] \
  || { echo "인스턴스 프로파일을 못 찾았다. IAM_PROFILE=... 로 지정하라." >&2; exit 1; }

# S3 권한 게이트. 없는 채로 띄우면 인스턴스가 조용히 죽고 로그도 S3 에 안 올라온다
# (로그 업로드 자체가 S3 를 쓴다). 띄우기 전에 여기서 잡는다.
ROLE=$(aws iam get-instance-profile --instance-profile-name "$PROFILE" \
  --query 'InstanceProfile.Roles[0].RoleName' --output text 2>/dev/null || true)
if [[ -n "$ROLE" && "$ROLE" != "None" ]]; then
  HAS_S3=$( { aws iam list-role-policies --role-name "$ROLE" --output text 2>/dev/null;
              aws iam list-attached-role-policies --role-name "$ROLE" \
                --query 'AttachedPolicies[].PolicyName' --output text 2>/dev/null; } \
            | grep -ci 's3' || true)
  if [[ "$HAS_S3" -eq 0 ]]; then
    echo "역할 ${ROLE} 에 S3 권한이 없다. 아래를 먼저 실행하라 (버킷 하나로 제한됨):" >&2
    echo "  aws iam put-role-policy --role-name ${ROLE} \\" >&2
    echo "    --policy-name dv-anatomy-s3 \\" >&2
    echo "    --policy-document file://cloud/iam-dv-anatomy-s3.json" >&2
    exit 1
  fi
fi

SUBNET="${SUBNET:-}"
if [[ -z "$SUBNET" ]]; then
  VPC=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true \
        --query 'Vpcs[0].VpcId' --output text)
  SUBNET=$(aws ec2 describe-subnets --region "$REGION" --filters Name=vpc-id,Values="$VPC" \
           --query 'Subnets[0].SubnetId' --output text)
fi
[[ -n "$SUBNET" && "$SUBNET" != "None" ]] || { echo "서브넷을 못 찾았다" >&2; exit 1; }

BRANCH="${BRANCH:-main}"
# ⚠️ mktemp 을 쓰면 안 된다. Git Bash 에서 /tmp/... 를 만들면 Windows 의 aws.exe 가
#    그 경로를 못 찾아 "Unable to load paramfile" 로 죽는다 (실제로 죽었다).
#    스크립트 옆에 상대 경로로 만들면 리눅스·Git Bash 양쪽에서 다 열린다.
UD="./.dv-userdata.$$"
trap 'rm -f "$UD"' EXIT
# ⚠️ user-data 는 ASCII 로만 쓴다. 한글 주석을 넣었더니 Windows 의 aws.exe 가
#    cp949 로 디코드하려다 "text contents could not be decoded" 로 죽었다.
#    (이 파일의 나머지 주석은 한글이어도 된다 — aws 에 넘어가는 건 이 heredoc 뿐이다.)
cat > "$UD" <<EOF
#!/bin/bash
# Safety net: the instance terminates itself in 4h no matter how bootstrap ends.
shutdown -h +240
export HOME=/root USER=root LANG=C.UTF-8
export BUCKET="${BUCKET}" MODE="${MODE}" DV_TAG="${TAG}" AWS_REGION="${REGION}"
cd /root
apt-get update -qq && apt-get install -y -qq git curl >/dev/null 2>&1
git clone -q -b ${BRANCH} https://github.com/JeonDaehong/iceberg-dv-anatomy.git || exit 1
chmod +x /root/iceberg-dv-anatomy/cloud/*.sh
bash /root/iceberg-dv-anatomy/cloud/bootstrap.sh
EOF

echo "MODE=${MODE}  TYPE=${TYPE} (${ARCH})  TAG=${TAG}"
echo "  AMI     : ${AMI}"
echo "  subnet  : ${SUBNET}"
echo "  profile : ${PROFILE}"
echo "  disk    : ${DISK_GB}GB"
echo

ID=$(aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" --instance-type "$TYPE" --subnet-id "$SUBNET" \
  --iam-instance-profile "Name=${PROFILE}" \
  --instance-initiated-shutdown-behavior terminate \
  --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=${DISK_GB},VolumeType=gp3,DeleteOnTermination=true}" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${NAME}},{Key=project,Value=dv-anatomy}]" \
  --user-data "file://${UD}" \
  --query 'Instances[0].InstanceId' --output text)
rm -f "$UD"

echo "인스턴스: ${ID}"
echo
echo "진행 상황 보기 (bootstrap 이 단계마다 S3 로 로그를 올린다):"
echo "  aws s3 cp s3://${BUCKET}/logs/${TAG}.log - | tail -40"
echo "결과 내려받기 (끝난 뒤):"
echo "  aws s3 sync s3://${BUCKET}/results/${TAG}/ cloud-results/${TAG}/"
echo "직접 들어가 보기:"
echo "  aws ssm start-session --region ${REGION} --target ${ID}"
echo "즉시 종료:"
echo "  aws ec2 terminate-instances --region ${REGION} --instance-ids ${ID}"
