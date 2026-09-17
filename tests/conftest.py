"""Shared DynamoDB table fixture for unit tests (moto — no real AWS)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

TABLE_NAME = "MerchantOnboarding"
REGION = "us-east-1"


@pytest.fixture
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("TABLE_NAME", TABLE_NAME)
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("INTERNAL_TIMEOUT_SECONDS", "25")


@pytest.fixture
def dynamodb_table(aws_credentials):
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(
            TableName=TABLE_NAME,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
        )
        client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
        yield boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)


DOCUMENTS_BUCKET_NAME = "test-documents-bucket"


@pytest.fixture
def documents_bucket(dynamodb_table, monkeypatch):
    """S3 bucket for document tests. Depends on dynamodb_table so it reuses
    the same active moto mock_aws() context rather than opening a second one.
    """
    monkeypatch.setenv("DOCUMENTS_BUCKET", DOCUMENTS_BUCKET_NAME)
    client = boto3.client("s3", region_name=REGION)
    client.create_bucket(Bucket=DOCUMENTS_BUCKET_NAME)
    yield DOCUMENTS_BUCKET_NAME