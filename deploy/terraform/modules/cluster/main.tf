variable "name" { type = string }
variable "k8s_version" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "node_pools" {
  type = map(object({
    instance_types = list(string)
    min            = number
    max            = number
    desired        = number
    disk_gb        = number
    taint          = optional(string)
  }))
}

data "aws_iam_policy_document" "cluster_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${var.name}-cluster"
  assume_role_policy = data.aws_iam_policy_document.cluster_assume.json
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_kms_key" "secrets" {
  description         = "${var.name} EKS secrets envelope encryption"
  enable_key_rotation = true
}

resource "aws_eks_cluster" "this" {
  name     = var.name
  version  = var.k8s_version
  role_arn = aws_iam_role.cluster.arn
  vpc_config {
    subnet_ids              = var.subnet_ids
    endpoint_private_access = true
    endpoint_public_access  = false
  }
  encryption_config {
    resources = ["secrets"]
    provider { key_arn = aws_kms_key.secrets.arn }
  }
  enabled_cluster_log_types = ["api", "audit", "authenticator"] # audit logs retained (Section 7)
  depends_on                = [aws_iam_role_policy_attachment.cluster]
}

data "aws_iam_policy_document" "node_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${var.name}-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy",
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

resource "aws_eks_node_group" "pool" {
  for_each        = var.node_pools
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = each.key
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.subnet_ids
  instance_types  = each.value.instance_types
  disk_size       = each.value.disk_gb
  labels          = { "lap/pool" = each.key }
  scaling_config {
    min_size     = each.value.min
    max_size     = each.value.max
    desired_size = each.value.desired
  }
  dynamic "taint" {
    for_each = each.value.taint == null ? [] : [each.value.taint]
    content {
      key    = split("=", taint.value)[0]
      value  = split("=", taint.value)[1]
      effect = "NO_SCHEDULE"
    }
  }
  depends_on = [aws_iam_role_policy_attachment.node]
}

resource "aws_eks_addon" "ebs" {
  cluster_name = aws_eks_cluster.this.name
  addon_name   = "aws-ebs-csi-driver"
}

data "tls_certificate" "oidc" { url = aws_eks_cluster.this.identity[0].oidc[0].issuer }

resource "aws_iam_openid_connect_provider" "this" {
  url             = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.oidc.certificates[0].sha1_fingerprint]
}

output "name" { value = aws_eks_cluster.this.name }
output "oidc_provider_arn" { value = aws_iam_openid_connect_provider.this.arn }
output "oidc_issuer" { value = aws_eks_cluster.this.identity[0].oidc[0].issuer }
