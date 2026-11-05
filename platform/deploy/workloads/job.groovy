// Load this trusted Job DSL after provisioning an exclusive delta-deployer agent.
pipelineJob('delta-platform-deploy') {
    description('승인한 플랫폼 배포 계획만 실행합니다. 고객 저장소의 스크립트는 실행하지 않습니다.')
    parameters {
        stringParam('DELTA_RUN_ID', '', '플랫폼 실행 UUID')
        stringParam('DELTA_DEPLOYMENT', '', '승인한 배포 JSON (base64)')
    }
    definition {
        cps {
            script(new File('/opt/delta/workloads/Jenkinsfile').text)
            sandbox(true)
        }
    }
}
