
function backup_db() {
    var form = webix.ui({
        view: "window", 
        position: 'center',
        modal: true,
        head: "Backup Database",
        width: 550,
        body: {
            view: 'form',
            complexData: true,
            elements: [
                { view: 'template', template: "Dumping postgres."},
                { view: 'text', name: 'dumpname', label: "Dumpname" },
                {
                    cols:[
                        {
                            view:"button", value:"OK", css:"webix_primary", click: function() { 
                                var values = this.getParentView().getFormView().getValues();
                                webix.message('Dumping to: ' + values['dumpname']);
                                form.hide();
                                webix.ajax().get('/cicd/dump', {
                                    'name': current_details,
                                    'dumpname': values['dumpname'],
                                }).then(function(data) {
                                    form.hide();
                                    webix.message('Dumped to: ' + values['dumpname']);
                                }).fail(function(data) {
                                    alert(data.statusText);
                                    console.error(data.responseText);
                                });
                            }
                        },
                        { view:"button", value:"Cancel", click: function() {
                            form.hide();
                        }}
                    ]
                }
            ],
            on: {
                'onSubmit': function() {
                },
            }
        }
    });
    form.show();
}