
function show_reset_form(name) {
    var dumps_promise = webix.ajax().get("/cicd/possible_dumps");
    dumps_promise.then(function(dumps) {
        dumps = dumps.json();
        var form = webix.ui({
            view: "window", 
            position: 'center',
            modal: true,
            head: "Reset Instance",
            width: 550,
            body: {
                view: 'form',
                complexData: true,
                elements: [
                    { view:"combo", name: 'dump', label:"Dump", options: dumps },
                    { view: 'template', template: "Rebuilding the instance in background. Will take some time, until instance is up again."},
                    {
                        cols:[
                            { view:"button", value:"OK", css:"webix_primary", click: function() { 
                                    var values = this.getParentView().getFormView().getValues();
                                    webix.ajax().get('/cicd/trigger/rebuild', {
                                        'name': name,
                                        'dump': values.dump,
                                    }).then(function(data) {
                                        form.hide();
                                    }).fail(function(response) {
                                        alert(response.statusText);
                                        console.error(response.responseText);
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
    });
}