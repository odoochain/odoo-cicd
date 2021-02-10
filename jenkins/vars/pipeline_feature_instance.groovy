def call() {

    pipeline {
        agent any
        environment {
        DEMO='1.3'
        }
        stages {
            stage('Verify and copy pipeline files') {
                steps {
                    scmSkip(deleteBuild: false, skipPattern:'^.*\\[ci skip\\].*')
                    echo "This is build number $BUILD_NUMBER of demo $DEMO and the workspace is ${WORKSPACE} "
                    sh '''
                    echo "Using a multi-line shell step"
                    '''

                    sh '''
                        pwd
                        pipelineFilesDirectory="$(basename $PWD)"
                        pipelinePureName=$(echo $pipelineFilesDirectory | cut  -d'@' -f 1)
                        cp "../${pipelinePureName}@libs/odoojenkinspipelines/build.py" "../${pipelinePureName}/build.py"
                        cp "../${pipelinePureName}@libs/odoojenkinspipelines/build.sh" "../${pipelinePureName}/build.sh"
                        cd "../${pipelinePureName}"
                    ''' 
                }
            }
        stage('Clone build.py') {
                steps {
                    echo "GET Build.py"
                    checkout scm
                    sh '''
                    SUBDIR=.pipelines
                    [ -d "$SUBDIR" ] && rm -Rf "$SUBDIR"
                    git clone git:odoodev/odoojenkinspipelines.git "$SUBDIR"
                    echo "Using sha of pipelines"
                    cd "$SUBDIR"; git rev-parse  HEAD; cd ..
                    cp "$SUBDIR"/build.* .
                    chmod +x build.sh
                    '''
                }
            }
            stage('Build') {
                stages {
                        stage('Build kept') {
                            steps {
                                sh './build.sh kept'
                            }
                        }
                        stage('Build live') {
                            steps {
                                sh './build.sh live'
                            }
                        }
                        stage('Build demo') {
                            steps {
                                sh './build.sh demo'
                        }

                    }
                }
            }
            stage('Unit Test') {
                steps {
                echo "Unit test"
                }
            }
            stage('Smoke Test and security Test') {

                parallel {
                    stage('First container scanner') {
                        steps {
                            echo "Building release ..."
                        }
                    }
                    stage('Second container scanner') {
                        steps {
                            echo "Building release ..."
                        }
                    }

                }
            }
            stage('Deploy'){

                steps {
                    echo "Deploying release ....."
                }
            }
        }
        post{
            always {
                echo 'Prints whether deploy happened or not, success or failure'
            }
        }
    }
}
