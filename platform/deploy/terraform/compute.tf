resource "aws_iam_role" "app" {

  name = "${var.name}-app"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "ec2.amazonaws.com"
      }, Action = "sts:AssumeRole"
    }]
  })

}
resource "aws_iam_role_policy_attachment" "app_ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "app" {

  role = aws_iam_role.app.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*"
      },
      {
        Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = aws_ecr_repository.platform.arn
      },
      {
        Effect = "Allow", Action = ["ssm:GetParameter"], Resource = "arn:aws:ssm:${var.region}:${var.account_id}:parameter/${var.name}/runtime"
      },
      {
        Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject"], Resource = "${aws_s3_bucket.backup.arn}/database/*"
      },
      {
        Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.backup.arn}/releases/*"
      }
    ]
  })

}
resource "aws_iam_instance_profile" "app" {
  name = "${var.name}-app"
  role = aws_iam_role.app.name
}
resource "aws_iam_role" "nat" {

  name = "${var.name}-nat"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "ec2.amazonaws.com"
      }, Action = "sts:AssumeRole"
    }]
  })

}
resource "aws_iam_role_policy_attachment" "nat_ssm" {
  role       = aws_iam_role.nat.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_instance_profile" "nat" {
  name = "${var.name}-nat"
  role = aws_iam_role.nat.name
}
resource "aws_instance" "nat" {

  ami                    = var.ami_id
  instance_type          = "t4g.micro"
  subnet_id              = aws_subnet.public.id
  source_dest_check      = false
  vpc_security_group_ids = [aws_security_group.nat.id]
  iam_instance_profile   = aws_iam_instance_profile.nat.name
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  credit_specification {
    cpu_credits = "standard"
  }
  root_block_device {
    volume_size = 8
    volume_type = "gp3"
    encrypted   = true
  }
  user_data                   = file("${path.module}/templates/nat.sh")
  user_data_replace_on_change = true
  tags = {
    Name = "${var.name}-nat"
  }
  depends_on = [aws_route.internet, aws_vpc_security_group_egress_rule.nat_https]

}
resource "aws_eip" "nat" {
  domain     = "vpc"
  instance   = aws_instance.nat.id
  depends_on = [aws_internet_gateway.platform]
}
resource "aws_ebs_volume" "data" {

  availability_zone = var.availability_zone
  size              = var.data_volume_size
  type              = "gp3"
  encrypted         = true
  tags = {
    Name = "${var.name}-database"
  }
  lifecycle {
    prevent_destroy = true
  }

}
resource "aws_instance" "app" {

  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.private.id
  associate_public_ip_address = false
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.app.name
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  credit_specification {
    cpu_credits = "standard"
  }
  root_block_device {
    volume_size = 12
    volume_type = "gp3"
    encrypted   = true
  }
  user_data = templatefile("${path.module}/templates/app.sh.tftpl", {
    volume_id = aws_ebs_volume.data.id
  })
  user_data_replace_on_change = true
  tags = {
    Name = "${var.name}-app"
  }
  depends_on = [aws_route.nat, aws_eip.nat, aws_vpc_security_group_egress_rule.app_https]

}
resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.app.id
}
