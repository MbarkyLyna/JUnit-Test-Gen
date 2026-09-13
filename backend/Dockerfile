# Build one image per JDK major version so docker_runner.py can pick the
# image that matches each uploaded project's actual Java version (Step 2).
#
# Build one tag per version referenced in docker_runner.DOCKER_IMAGES:
#   docker build --build-arg JAVA_VERSION=8  -t spring-test-gen-sandbox:jdk8  -f docker/Dockerfile.sandbox .
#   docker build --build-arg JAVA_VERSION=11 -t spring-test-gen-sandbox:jdk11 -f docker/Dockerfile.sandbox .
#   docker build --build-arg JAVA_VERSION=17 -t spring-test-gen-sandbox:jdk17 -f docker/Dockerfile.sandbox .
#   docker build --build-arg JAVA_VERSION=21 -t spring-test-gen-sandbox:jdk21 -f docker/Dockerfile.sandbox .
#
# If you add a version to DOCKER_IMAGES in docker_runner.py, build a matching
# image here or resolve_docker_image() will fall back to the nearest one it
# has, which may not actually match the project's bytecode target.

ARG JAVA_VERSION=17
FROM eclipse-temurin:${JAVA_VERSION}-jdk

ARG MAVEN_VERSION=3.9.9
RUN apt-get update && apt-get install -y --no-install-recommends curl tar \
    && curl -fsSL "https://archive.apache.org/dist/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz" \
       -o /tmp/maven.tar.gz \
    && tar -xzf /tmp/maven.tar.gz -C /opt \
    && ln -s /opt/apache-maven-${MAVEN_VERSION} /opt/maven \
    && rm /tmp/maven.tar.gz \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/maven/bin:${PATH}"

RUN useradd -m -u 1000 appuser
USER appuser
WORKDIR /workspace