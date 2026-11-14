// Constraints de unicidade: e o que torna o MERGE do loader idempotente
// (rodar duas vezes nao duplica no). Cypher nativo do Neo4j 5.26, sem APOC.
CREATE CONSTRAINT tenant_id IF NOT EXISTS FOR (n:Tenant) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT category_id IF NOT EXISTS FOR (n:Category) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT product_id IF NOT EXISTS FOR (n:Product) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT campaign_id IF NOT EXISTS FOR (n:Campaign) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT policy_id IF NOT EXISTS FOR (n:Policy) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT policy_version_id IF NOT EXISTS FOR (n:PolicyVersion) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE;
