const __get_repositories = {
    "body": {
        "repositories": [
            {
                "slug": "bbit-trnxd-orders-api",
                "tags": ["batch", "fargate", "step-function", "lambda"],
                "sources": [
                    {
                        "branch": "release/REP-325073",
                        "url": "https://bitbucket.org/my_org_web_dev/bbit-trnxd-orders-api/branch/release/REP-325073",
                        "head_commit": "abc123",
                        "tags": [
                            {
                                "name": "uat-1201",
                                "url": "https://app.circleci.com/pipelines/bb/my_org_web_dev/bbit-trnxd-orders-api/10/details?workflowId=f3c90a0f-b188-48c7-8dab-4cf51e57fa4c"
                            }
                        ],
                        "targets": [
                            {
                                "branch": "master",
                                "head_commit": "def456",
                                "pr": {
                                    "status": "open",
                                    "title": "Fix order processing bug",
                                    "url": "https://bitbucket.org/my_org_web_dev/bbit-test-02/pull-requests/3",
                                },
                                "ssm": ["/config/common/amount-round", "/config/common/amount-calculate"]
                            }
                        ],
                        "ssm": ["/config/common/ledger/database-user"]
                    }
                ]
            }
        ]
    },
    "status": {
        "code": "BBIT-000",
        "description": "Todas transacciones realizadas correctamente"
    }
};