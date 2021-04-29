How to setup:

  - clone to /home/anyuser/cicd
  - adapt /home/anyuser/cicd/cicd-apps/.env
  - cd /home/anyuser/cicd; docker-compose up -d
  - make a git repository for your pipelines file: /home/anyuser/cicd-pipeline
    - git init
    - add default Jenkinsfile from this repo
  - make jenkins multibranch to build pipeline:
    - Remote Jenkinsfile addin (configure to path)
    - setup webhook with git repo



X