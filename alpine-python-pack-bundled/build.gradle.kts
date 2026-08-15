plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "dev.alpine.pythonpack.bundled"
    compileSdk = 36

    defaultConfig {
        minSdk = 26
        consumerProguardFiles("consumer-rules.pro")
    }

    sourceSets {
        getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/distribution/assets"))
    }

    androidResources {
        noCompress += "apk"
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
    testImplementation("junit:junit:4.13.2")
}

val pythonPackSource = providers.environmentVariable("ALPINE_PYTHON_PACKAGE_DIR")
    .orElse(rootProject.layout.projectDirectory.dir("alpine-python-pack-bundled/src/main/python-pack").asFile.absolutePath)
val generatedAssetRoot = layout.buildDirectory.dir("generated/distribution/assets")
val generatedPackDirectory = generatedAssetRoot.map { it.dir("alpine-python-pack") }

val preparePythonPackagePackAssets by tasks.registering(Exec::class) {
    group = "build setup"
    description = "Packages a local, production-marked Alpine Python pack without network access."
    inputs.property("pythonPackSource", pythonPackSource)
    val source = file(pythonPackSource.get())
    if (source.isDirectory) {
        inputs.dir(source)
    }
    inputs.file(rootProject.layout.projectDirectory.file("scripts/python_package_pack.py"))
    inputs.file(rootProject.layout.projectDirectory.file("scripts/verify-python-package-pack.py"))
    outputs.dir(generatedPackDirectory)
    commandLine(
        providers.environmentVariable("PYTHON").orElse("python3").get(),
        rootProject.file("scripts/verify-python-package-pack.py").absolutePath,
        "--prepare",
        "--source",
        source.absolutePath,
        "--output",
        generatedPackDirectory.get().asFile.absolutePath,
    )
}

val verifyProductionPythonPackagePack by tasks.registering(Exec::class) {
    group = "verification"
    description = "Fails closed unless generated assets contain a locked production Python pack."
    dependsOn(preparePythonPackagePackAssets)
    inputs.dir(generatedPackDirectory)
    commandLine(
        providers.environmentVariable("PYTHON").orElse("python3").get(),
        rootProject.file("scripts/verify-python-package-pack.py").absolutePath,
        "--verify-assets",
        "--output",
        generatedPackDirectory.get().asFile.absolutePath,
        "--require-production",
    )
}

tasks.configureEach {
    if (
        (name.startsWith("merge") && name.endsWith("Assets")) ||
        name.lowercase().contains("lint")
    ) {
        dependsOn(preparePythonPackagePackAssets)
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        jvmDefault.set(org.jetbrains.kotlin.gradle.dsl.JvmDefaultMode.DISABLE)
    }
}
