# Multi-Line Documentation

```robotframework
Test Case 1
    [Documentation]
        | = Example Heading =
		| *bold* _italic_
        | Lorem ipsum dolor sit amet, consectetur adipisicing elit,
        | do eiusmod tempor incididunt ut labore et dolore sed magna
        | aliqua. Ut enim ad minim veniam, quis nostrud exercitation
        | ullamco laboris nisi ut aliquip ex ea commodo consequat.
```

# Dictionaries

```robotframework
${values}=      Create Dictionary
                ...   name=${name}
                ...   is_docker_host=True
                ...   external_url=http://testsite
                ...   ttype=dev
                ...   ssh_user=${ROBOTTEST_SSH_USER}
                ...   ssh_pubkey=${ROBOTTEST_SSH_PUBKEY}
                ...   ssh_key=${ROBOTTEST_SSH_KEY}
                ...   postgres_server_id=${postgres}
```

# x-nary
```robotframework
${decimalval} =   Set variable If
...               '${decimalval}'=='0'       //md-option[@value='0dp']
...               '${decimalval}'=='1'       //md-option[@value='1dp']
...               '${decimalval}'=='2'       //md-option[@value='2dp']
```