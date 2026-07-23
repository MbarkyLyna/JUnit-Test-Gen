FROM maven:3.9.6-eclipse-temurin-17-alpine

# Create non-root user and group
RUN addgroup -g 1000 appgroup && \
    adduser -u 1000 -G appgroup -D appuser

WORKDIR /workspace

# Prepare maven repository directory with non-root permissions
RUN mkdir -p /home/appuser/.m2 && chown -R appuser:appgroup /workspace /home/appuser

# Pre-warm common plugins (JaCoCo 0.8.12, Surefire, Compiler, JUnit 5) into image .m2 cache
USER appuser
RUN mvn org.apache.maven.plugins:maven-dependency-plugin:3.6.1:get -Dartifact=org.jacoco:jacoco-maven-plugin:0.8.12 -DremoteRepositories=https://repo.maven.apache.org/maven2 || true
RUN mvn org.apache.maven.plugins:maven-dependency-plugin:3.6.1:get -Dartifact=org.apache.maven.plugins:maven-surefire-plugin:3.2.5 -DremoteRepositories=https://repo.maven.apache.org/maven2 || true
RUN mvn org.apache.maven.plugins:maven-dependency-plugin:3.6.1:get -Dartifact=org.junit.jupiter:junit-jupiter:5.10.2 -DremoteRepositories=https://repo.maven.apache.org/maven2 || true

ENTRYPOINT ["mvn"]
