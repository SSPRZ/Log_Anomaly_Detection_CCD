env    = "staging"
region = "eu-west-1"
node_pools = {
  kafka   = { instance_types = ["m6i.large"], min = 1, max = 1, desired = 1, disk_gb = 50, taint = "dedicated=kafka" }
  compute = { instance_types = ["c6i.xlarge"], min = 1, max = 3, desired = 2, disk_gb = 50 }
  storage = { instance_types = ["r6i.large"], min = 1, max = 1, desired = 1, disk_gb = 50, taint = "dedicated=clickhouse" }
}
