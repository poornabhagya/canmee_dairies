variable "alert_email" {
  description = "DevOps alert notification email address"
  type        = string
}

variable "production_instance_id" {
  description = "Production EC2 instance ID to monitor"
  type        = string
}

# 1. SNS Topic එක නිර්මාණය කිරීම
resource "aws_sns_topic" "devops_alerts" {
  name = "canmee-devops-alerts-topic"

  tags = {
    Environment = "Production"
    Project     = "Canmee Dairies"
  }
}

# 2. Email Subscription එක එක් කිරීම
resource "aws_sns_topic_subscription" "email_alert" {
  topic_arn = aws_sns_topic.devops_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# 3. CloudWatch Alarm: High CPU Utilization (>= 85%)
resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "canmee-production-high-cpu-alarm"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 300
  statistic           = "Average"
  threshold           = 85
  alarm_description   = "Triggered when Production EC2 CPU exceeds 85% for 10 minutes"
  alarm_actions       = [aws_sns_topic.devops_alerts.arn]

  dimensions = {
    InstanceId = var.production_instance_id
  }
}

# 4. CloudWatch Alarm: EC2 Status Check Failed
resource "aws_cloudwatch_metric_alarm" "instance_status_check" {
  alarm_name          = "canmee-production-instance-status-alarm"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1
  alarm_description   = "Triggered when Production EC2 instance or system status check fails"
  alarm_actions       = [aws_sns_topic.devops_alerts.arn]

  dimensions = {
    InstanceId = var.production_instance_id
  }
}

output "sns_topic_arn" {
  value = aws_sns_topic.devops_alerts.arn
}
