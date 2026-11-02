# Origem transacional dos pedidos. O status ATUAL de um pedido mora aqui,
# nunca no corpus: o documento so explica o que cada status significa.
resource "aws_dynamodb_table" "orders" {
  name         = "${var.project}-orders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "order_id"

  attribute {
    name = "order_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}

# Carrega os pedidos ficticios de orders/fixtures.json. Mudar o arquivo e
# rodar o pipeline atualiza a tabela, sem script manual.
resource "aws_dynamodb_table_item" "fixtures" {
  for_each   = local.fixtures.orders
  table_name = aws_dynamodb_table.orders.name
  hash_key   = aws_dynamodb_table.orders.hash_key

  item = jsonencode({
    order_id    = { S = each.key }
    customer_id = { S = each.value.customer_id }
    status      = { S = each.value.status }
    updated_at  = { S = each.value.updated_at }
  })
}
