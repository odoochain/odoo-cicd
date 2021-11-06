{
    'name': 'cicd',
    'version': '14.0.1.0',
    'auto_install': True,
    'depends': ['base'],
    "external_dependencies": {
        "python": [
            "pudb",
            'spur',
            'bson',
        ]
    },
    'data': [
        'data/data.xml',
        'data/cronjobs.xml',
        'security/ir.model.access.csv',
        'views/repository.xml',
        'views/git_branch_form.xml',
        'views/git_branch_tree.xml',
        'views/git_branch_kanban.xml',
        'views/menu.xml',
    ],
}