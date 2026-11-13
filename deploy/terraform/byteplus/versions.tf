terraform {
  required_version = ">= 1.9, < 2.0"

  required_providers {
    bytepluscc = {
      source  = "byteplus-sdk/bytepluscc"
      version = "= 0.0.63"
    }
  }
}

provider "bytepluscc" {
  region = var.region
}
