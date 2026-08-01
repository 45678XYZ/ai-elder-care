# Bedrock 模型呼叫權限。
# 模型走 global／區域 inference profile 時，實際被叫用的 foundation model ARN 會隨
# 路由的目的區域改變，因此這裡不逐一列出模型 ARN；改由環境變數控制
# 實際使用哪個模型，並以帳號層級的模型存取權（Bedrock model access）把關。
data "aws_iam_policy_document" "bedrock_invoke" {
  statement {
    sid    = "InvokeExtractionModels"
    effect = "Allow"

    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream",
    ]

    resources = ["*"]
  }
}

resource "aws_iam_policy" "bedrock_invoke" {
  name        = "${var.project_name}-bedrock-invoke"
  description = "萃取 pipeline 呼叫 Bedrock 對話模型"
  policy      = data.aws_iam_policy_document.bedrock_invoke.json
}
