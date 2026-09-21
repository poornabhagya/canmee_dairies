# 1. Module Input Variables
variable "origin_domain_name" {
  description = "Production EC2 Public IP or DNS"
  type        = string
}

variable "certificate_arn" {
  description = "ACM Certificate ARN from us-east-1"
  type        = string
}

variable "domain_aliases" {
  description = "Custom domain aliases for CloudFront"
  type        = list(string)
  default     = []
}

# 2. CloudFront Edge Distribution Resource එක
resource "aws_cloudfront_distribution" "prod_distribution" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "Canmee Dairies Production Edge Distribution"
  price_class     = "PriceClass_All"

  aliases = var.domain_aliases

  origin {
    domain_name = var.origin_domain_name
    origin_id   = "CanmeeProdEC2Origin"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # Default Cache Behavior (Django Dynamic Routes / Web traffic)
  default_cache_behavior {
    allowed_methods  = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "CanmeeProdEC2Origin"

    forwarded_values {
      query_string = true
      headers      = ["Host", "Authorization", "X-CSRFToken"]

      cookies {
        forward = "all"
      }
    }

    viewer_protocol_policy = "redirect-to-https"
    min_ttl                = 0
    default_ttl            = 0
    max_ttl                = 0
  }

  # Static Assets Cache Behavior (/static/*)
  ordered_cache_behavior {
    path_pattern     = "/static/*"
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "CanmeeProdEC2Origin"

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    viewer_protocol_policy = "redirect-to-https"
    min_ttl                = 86400
    default_ttl            = 604800
    max_ttl                = 31536000
    compress               = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Custom SSL Certificate එක සම්බන්ධ කිරීම (TLS 1.2/1.3)
  viewer_certificate {
    acm_certificate_arn      = var.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  tags = {
    Environment = "Production"
    Project     = "Canmee Dairies"
  }
}

# 3. Outputs
output "cloudfront_domain_name" {
  description = "CloudFront Distribution Domain Name (.cloudfront.net)"
  value       = aws_cloudfront_distribution.prod_distribution.domain_name
}

output "cloudfront_hosted_zone_id" {
  description = "CloudFront Route 53 Zone ID (if needed later)"
  value       = aws_cloudfront_distribution.prod_distribution.hosted_zone_id
}
