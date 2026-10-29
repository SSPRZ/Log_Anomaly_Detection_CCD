variable "name" { type = string }
variable "archive_expire_days" { type = number }
variable "oidc_provider_arn" { type = string }
variable "oidc_issuer" { type = string }
variable "namespace" { type = string }

resource "aws_kms_key" "data" {
  description         = "${var.name} data at rest"
  enable_key_rotation = true
}

resource "aws_s3_bucket" "archive" { bucket = "${var.name}-logs-archive" }
resource "aws_s3_bucket" "checkpoints" { bucket = "${var.name}-flink-checkpoints" }
resource "aws_s3_bucket" "mlflow" { bucket = "${var.name}-mlflow" }

locals {
  buckets = {
    archive     = aws_s3_bucket.archive.id
    checkpoints = aws_s3_bucket.checkpoints.id
    mlflow      = aws_s3_bucket.mlflow.id
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets
  bucket   = each.value
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = local.buckets
  bucket                  = each.value
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 1-year lifecycle rule on the Parquet archive (Section 4.4)
resource "aws_s3_bucket_lifecycle_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    expiration { days = var.archive_expire_days }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "checkpoints" {
  bucket = aws_s3_bucket.checkpoints.id
  rule {
    id     = "expire-old-checkpoints"
    status = "Enabled"
    filter {}
    expiration { days = 7 }
  }
}

# IRSA role for the platform stages / Flink jobs: read+write only these buckets
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }
    condition {
      test     = "StringLike"
      variable = "${replace(var.oidc_issuer, "https://", "")}:sub"
      values   = ["system:serviceaccount:${var.namespace}:lap", "system:serviceaccount:flink:flink"]
    }
  }
}

resource "aws_iam_role" "stages" {
  name               = "${var.name}-stages"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "buckets" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = flatten([for b in values(local.buckets) : ["arn:aws:s3:::${b}", "arn:aws:s3:::${b}/*"]])
  }
  statement {
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.data.arn]
  }
}

resource "aws_iam_role_policy" "buckets" {
  role   = aws_iam_role.stages.id
  policy = data.aws_iam_policy_document.buckets.json
}

output "archive_bucket" { value = aws_s3_bucket.archive.id }
output "checkpoint_bucket" { value = aws_s3_bucket.checkpoints.id }
output "stages_role_arn" { value = aws_iam_role.stages.arn }
