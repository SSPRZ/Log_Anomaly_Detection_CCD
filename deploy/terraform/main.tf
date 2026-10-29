# Cloud infrastructure for the production target (Section 5.1): network, managed Kubernetes across
# 3 zones with separate node pools for Kafka, compute and storage, object storage buckets and IAM.
# AWS flavour; GKE/AKS variants swap the modules below.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.70" }
    tls = { source = "hashicorp/tls", version = "~> 4.0" }
  }
  backend "s3" {} # configured per environment: terraform init -backend-config=envs/<env>.backend.hcl
}

provider "aws" {
  region = var.region
  default_tags { tags = { project = "log-anomaly-platform", env = var.env, managed_by = "terraform" } }
}

data "aws_availability_zones" "available" { state = "available" }

locals {
  azs  = slice(data.aws_availability_zones.available.names, 0, 3)
  name = "lap-${var.env}"
}

module "network" {
  source = "./modules/network"
  name   = local.name
  cidr   = var.vpc_cidr
  azs    = local.azs
}

module "cluster" {
  source      = "./modules/cluster"
  name        = local.name
  k8s_version = var.k8s_version
  vpc_id      = module.network.vpc_id
  subnet_ids  = module.network.private_subnet_ids
  node_pools  = var.node_pools
}

module "storage" {
  source              = "./modules/storage"
  name                = local.name
  archive_expire_days = var.archive_expire_days
  oidc_provider_arn   = module.cluster.oidc_provider_arn
  oidc_issuer         = module.cluster.oidc_issuer
  namespace           = "lap"
}

output "cluster_name" { value = module.cluster.name }
output "archive_bucket" { value = module.storage.archive_bucket }
output "checkpoint_bucket" { value = module.storage.checkpoint_bucket }
output "stages_role_arn" { value = module.storage.stages_role_arn }
