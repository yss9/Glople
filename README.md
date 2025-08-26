<h1>Glople</h1>

Git Clone 후 application.properties 설정(Mysql)

src/main/resources/application.properties<br>
없을 시 resources 폴더 생성 후 application.properties 파일 생성<br>
Mysql **스키마 생성 후** 아래 코드를 복사해서 [ ]안에 본인 설정 정보 수정

<pre>
<code>
application.properties
spring.datasource.driver-class-name=com.mysql.cj.jdbc.Driver
spring.datasource.url=jdbc:mysql://localhost:3306/[스키마이름]
spring.datasource.username=[mysql 이름 ex)root]
spring.datasource.password=[mysql 비밀번호]
spring.jpa.show-sql=true
spring.jpa.hibernate.ddl-auto=update
spring.jpa.properties.hibernate.format_sql=true
spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.MySQL8Dialect
</code>
</pre>

<hr>

<h2>  Git bash 초기 설정 </h2>
git config --global user.name "git 이름" <br>
git config --global user.email "git 로그인 이메일" <br>
git config --global --add push.autoSetupRemote true <br>
git push --set-upstream origin main <br>


<h2> Git 작업 순서 </h2>
git pull <br>
git checkout -b 브랜치이름 <br>
--코드작업--<br>
--작업 끝--<br>
git add .<br>
git commit -m "커밋 메시지"<br>
git push<br>
git checkout main ->메인으로 브랜치 이동<br>
git merge 브랜치이름 (충돌나면 수정)<br>
git push<br>

**충돌 시 충돌나는 본인 코드 주석 후 merge하고 다시 코드  수정**
