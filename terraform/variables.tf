variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Resource name prefix used to keep this stack's resources identifiable/non-colliding."
  type        = string
  default     = "kafka-simple-tf"
}

variable "iac_tag" {
  description = "Value for the Iac tag."
  type        = string
  default     = "tf"
}

variable "project_tag" {
  description = "Value for the Project tag."
  type        = string
  default     = "kafka-simple-no-msk"
}
