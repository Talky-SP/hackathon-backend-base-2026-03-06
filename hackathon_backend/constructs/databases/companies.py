from aws_cdk import Tags, aws_dynamodb as dynamodb
from constructs import Construct
from hackathon_backend.constructs.base_class.base_class_dynamodb import BaseDynamoDB
from hackathon_backend.config.environments import Config


class CompaniesTable(BaseDynamoDB):
    def __init__(self, scope: Construct, id: str, config: Config):
        self.config = config
        table_name = f"{config.stage.capitalize()}_Companies"

        super().__init__(
            scope=scope,
            id=id,
            table_name=table_name,
            partition_key="PK",
            sort_key="SK",
            stream_type="NEW_AND_OLD_IMAGES",
            billing_mode=self.config.dynamodb_billing_mode,
            removal_policy=self.config.removal_policy,
        )

        N = dynamodb.AttributeType.NUMBER

        # GSI 1
        self.add_gsi("ByNamePrefixIndex", "company_name_prefix_4", "revenue", sk_type=N)
        # GSI 2
        self.add_gsi("ByNameWordIndex", "name_word_1", "revenue", sk_type=N)
        # GSI 3
        self.add_gsi("ByNameWord2Index", "name_word_2", "revenue", sk_type=N)
        # GSI 4
        self.add_gsi("ByFullNameIndex", "company_name_normalized", "revenue", sk_type=N)
        # GSI 5
        self.add_gsi("ByCityIndex", "city", "revenue", sk_type=N)
        # GSI 6
        self.add_gsi("ByProvinceIndex", "province", "revenue", sk_type=N)
        # GSI 7
        self.add_gsi("ByRevenueTierIndex", "revenue_tier", "revenue", sk_type=N)
        # GSI 8
        self.add_gsi("ByCnaeCodeIndex", "cnae_code", "revenue", sk_type=N)
        # GSI 9 - SK is String (company_name_normalized)
        self.add_gsi("ByCityAndNameIndex", "city", "company_name_normalized")
        # GSI 10
        self.add_gsi("ByCifIndex", "cif", "revenue", sk_type=N)

        for tag_key, tag_value in self.config.get_tags().items():
            Tags.of(self.table).add(tag_key, tag_value)
