# 한국어 형태소 분석기(nori)를 포함한 Elasticsearch 이미지.
# 플러그인을 실행 중인 컨테이너에 수동 설치하면 컨테이너 재생성 시 사라지므로
# (플러그인은 볼륨이 아니라 컨테이너 파일시스템에 설치됨) 이미지에 굽는다.
FROM docker.elastic.co/elasticsearch/elasticsearch:8.13.4

RUN bin/elasticsearch-plugin install --batch analysis-nori
