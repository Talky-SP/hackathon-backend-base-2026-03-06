"""
Agent Store — single-table DynamoDB for all agent data.

Access patterns:
  - Chats by location:   pk=LOC#{locationId}, sk=CHAT#{chatId}
  - Messages by chat:    pk=CHAT#{chatId},    sk=MSG#{timestamp}
  - Costs by chat:       pk=CHAT#{chatId},    sk=COST#{timestamp}
  - Traces by chat:      pk=CHAT#{chatId},    sk=TRACE#{timestamp}
  - Tasks by location:   pk=LOC#{locationId}, sk=TASK#{taskId}
  - Task steps:          pk=TASK#{taskId},    sk=STEP#{stepNumber}
  - Traces by task:      GSI1 gsi1pk=TASK#{taskId}, gsi1sk=TRACE#{ts}
  - Costs by location:   GSI1 gsi1pk=LOC#{locationId}#COST, gsi1sk=ts
  - Trace by ID:         GSI2 gsi2pk=traceId
"""
from aws_cdk import Tags
from constructs import Construct
from hackathon_backend.config.environments import Config
from hackathon_backend.constructs.base_class.base_class_dynamodb import BaseDynamoDB


class AgentStoreTable(Construct):
    def __init__(self, scope: Construct, id: str, config: Config):
        super().__init__(scope, id)

        self._db = BaseDynamoDB(
            self, "AgentStore",
            partition_key="pk",
            sort_key="sk",
            billing_mode=config.dynamodb_billing_mode,
            removal_policy=config.removal_policy,
        )
        self.table = self._db.table

        # GSI1: traces by taskId, costs by location
        self._db.add_gsi("gsi1", partition_key="gsi1pk", sort_key="gsi1sk")

        # GSI2: lookup trace by traceId
        self._db.add_gsi("gsi2", partition_key="gsi2pk")

        for k, v in config.get_tags().items():
            Tags.of(self.table).add(k, v)
