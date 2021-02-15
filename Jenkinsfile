pipeline {
    agent any
    environment {
      BUILD_SCRIPTS='/opt/odoo-cicd-jenkins/build-scripts'
    }
    stages {
      stage('Make Instances') {
        steps {
          sh '''
          "$BUILD_SCRIPTS/run.py" build --key=kept 
          '''
        }
        steps {
          sh '''
          "$BUILD_SCRIPTS/run.py" build --key=demo 
          '''
        }
        steps {
          sh '''
          "$BUILD_SCRIPTS/run.py" build --key=live 
          '''
        }
      }
    }
  }
