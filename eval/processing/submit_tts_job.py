"""提交 TTS 評估 Processing Job 到 SageMaker

從本地執行，job 在雲端 ml.g5.xlarge (A10G 24GB) 上跑。
"""

import boto3
import json
import time
from datetime import datetime

ROLE_ARN = "arn:aws:iam::437814057855:role/SageMakerEvalRole"
REGION = "us-west-2"
BUCKET = "e-hakka-care-eval-437814057855"
JOB_NAME = f"tts-eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

# PyTorch 2.1 GPU DLC image (us-west-2)
IMAGE_URI = "763104351884.dkr.ecr.us-west-2.amazonaws.com/pytorch-inference:2.1.0-gpu-py310-cu118-ubuntu20.04-sagemaker"

sm = boto3.client("sagemaker", region_name=REGION)

print(f"提交 Processing Job: {JOB_NAME}")
print(f"  Instance: ml.g5.xlarge (A10G 24GB)")
print(f"  Image: {IMAGE_URI}")
print(f"  Input: s3://{BUCKET}/input/")
print(f"  Output: s3://{BUCKET}/output/{JOB_NAME}/")

sm.create_processing_job(
    ProcessingJobName=JOB_NAME,
    ProcessingResources={
        "ClusterConfig": {
            "InstanceCount": 1,
            "InstanceType": "ml.g5.xlarge",
            "VolumeSizeInGB": 50,
        }
    },
    AppSpecification={
        "ImageUri": IMAGE_URI,
        "ContainerEntrypoint": ["python3", "/opt/ml/processing/code/tts_eval.py"],
    },
    RoleArn=ROLE_ARN,
    ProcessingInputs=[
        {
            "InputName": "input",
            "S3Input": {
                "S3Uri": f"s3://{BUCKET}/input/",
                "LocalPath": "/opt/ml/processing/input",
                "S3DataType": "S3Prefix",
                "S3InputMode": "File",
            },
        },
        {
            "InputName": "code",
            "S3Input": {
                "S3Uri": f"s3://{BUCKET}/code/tts_eval.py",
                "LocalPath": "/opt/ml/processing/code",
                "S3DataType": "S3Prefix",
                "S3InputMode": "File",
            },
        },
    ],
    ProcessingOutputConfig={
        "Outputs": [
            {
                "OutputName": "output",
                "S3Output": {
                    "S3Uri": f"s3://{BUCKET}/output/{JOB_NAME}/",
                    "LocalPath": "/opt/ml/processing/output",
                    "S3UploadMode": "EndOfJob",
                },
            }
        ]
    },
    StoppingCondition={"MaxRuntimeInSeconds": 3600},
)

print(f"\n✅ Job 已提交: {JOB_NAME}")
print("追蹤狀態...")

while True:
    resp = sm.describe_processing_job(ProcessingJobName=JOB_NAME)
    status = resp["ProcessingJobStatus"]
    print(f"  [{time.strftime('%H:%M:%S')}] {status}")
    if status in ("Completed", "Failed", "Stopped"):
        break
    time.sleep(30)

if status == "Completed":
    print(f"\n✅ Job 完成！")
    print(f"   結果: s3://{BUCKET}/output/{JOB_NAME}/")
elif status == "Failed":
    print(f"\n✗ Job 失敗")
    print(f"  原因: {resp.get('FailureReason', 'unknown')}")
    print(f"  ExitMessage: {resp.get('ExitMessage', 'none')}")
