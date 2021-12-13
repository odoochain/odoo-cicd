{   'application': False,
    'auto_install': True,
    'css': [],
    'data': [   'security/groups.xml',
                'data/cronjobs.xml',
                'data/data.xml',
                'data/queuejob_functions.xml',
                'views/commit_form.xml',
                'views/compressor_tree.xml',
                'views/dump_form.xml',
                'views/dump_tree.xml',
                'views/git_branch_form.xml',
                'views/git_branch_kanban.xml',
                'views/git_branch_search.xml',
                'views/git_branch_tree.xml',
                'views/machine_form.xml',
                'views/postgres_form.xml',
                'views/postgres_tree.xml',
                'views/registry.xml',
                'views/release_form.xml',
                'views/release_item_form.xml',
                'views/release_search.xml',
                'views/release_tree.xml',
                'views/repository.xml',
                'views/task_form.xml',
                'views/test_run_form.xml',
                'views/test_run_kanban.xml',
                'views/test_run_search.xml',
                'views/test_run_tree.xml',
                'views/user_form.xml',
                'views/volume_form.xml',
                'views/menu.xml',
                'security/ir.model.access.csv'],
    'demo': [],
    'depends': ['mail', 'queue_job'],
    'external_dependencies': {   'python': [   'pudb',
                                               'spur',
                                               'spurplus',
                                               'bson',
                                               'humanize',
                                               'paramiko']},
    'name': 'cicd',
    'qweb': [],
    'test': [],
    'version': '14.0.1.0'}
