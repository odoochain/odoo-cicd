
function delete_instance_ask() {
    debugger;
    var form_reset = webix.ui({
        view: "window", 
        position: 'center',
        modal: true,
        head: "Delete Instance",
        width: 550,
        body: {
            view: 'form',
            complexData: true,
            elements: [
                // { view:"combo", name: 'dump', label:"Dump", options: dumps },
                { view: 'template', template: "Going to erase" + current_details},
                {
                    cols:[
                        { view:"button", value:"OK", css:"webix_primary", click: function() { 
                                    var values = this.getParentView().getFormView().getValues();
                                    form_reset.hide();
                                    delete_instance(current_details);
                                }
                        },
                        { view:"button", value:"Cancel", click: function() {
                            form_reset.hide();
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
    form_reset.show();
}