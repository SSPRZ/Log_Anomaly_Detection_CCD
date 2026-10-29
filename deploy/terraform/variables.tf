variable "env" {
  type        = string
  description = "staging | prod"
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "k8s_version" {
  type    = string
  default = "1.31"
}

variable "archive_expire_days" {
  type    = number
  default = 365
}

# Capacity plan (Section 5.4): ~70 vCPU / 260 GB across three pools.
variable "node_pools" {
  type = map(object({
    instance_types = list(string)
    min            = number
    max            = number
    desired        = number
    disk_gb        = number
    taint          = optional(string)
  }))
  default = {
    kafka   = { instance_types = ["r6i.xlarge"], min = 3, max = 3, desired = 3, disk_gb = 100, taint = "dedicated=kafka" }
    compute = { instance_types = ["c6i.2xlarge"], min = 3, max = 8, desired = 3, disk_gb = 100 }
    storage = { instance_types = ["r6i.2xlarge"], min = 2, max = 2, desired = 2, disk_gb = 100, taint = "dedicated=clickhouse" }
  }
}
