plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "dev.alpine.codexclient"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.alpine.codexclient"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        manifestPlaceholders["appLabel"] = "Alpine Codex Client"
        ndk { abiFilters += "arm64-v8a" }
    }

    buildTypes {
        getByName("debug") {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            manifestPlaceholders["appLabel"] = "Alpine Codex Client (Debug)"
        }
    }

    buildFeatures {
        compose = true
    }

    // The installer copies the bundled rootfs as an asset and executes PRoot
    // from the native-library directory inside this debug-only APK.
    androidResources {
        noCompress += "asset"
    }

    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        disable += "AndroidGradlePluginVersion"
    }
}

dependencies {
    implementation(project(":alpine-runtime-api"))
    implementation(project(":alpine-runtime-android"))
    implementation(project(":alpine-runtime-host"))
    implementation(project(":alpine-runtime-background-android"))
    implementation(project(":alpine-runtime-ui-compose"))
    implementation(project(":alpine-runtime-pack-bundled"))
    implementation(project(":alpine-workspace-api"))
    implementation(project(":alpine-workspace-android"))
    implementation(project(":codex-cli-pack"))
    implementation(project(":codex-gateway-pack-bundled"))
    implementation(project(":codex-runtime-bridge"))

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.ktx)
    implementation(libs.kotlinx.coroutines.android)

    debugImplementation(libs.androidx.compose.ui.tooling)
    debugImplementation(libs.androidx.compose.ui.test.manifest)

    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.espresso.core)
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        jvmDefault.set(org.jetbrains.kotlin.gradle.dsl.JvmDefaultMode.DISABLE)
    }
}
