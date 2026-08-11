pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "alpine-codex-cli-client"
include(":app")
include(":alpine-runtime-api")
include(":alpine-runtime-android")
include(":alpine-runtime-host")
include(":alpine-runtime-background-android")
include(":alpine-runtime-ui-compose")
include(":alpine-runtime-pack-bundled")
include(":alpine-workspace-api")
include(":alpine-workspace-android")
include(":codex-cli-pack")
