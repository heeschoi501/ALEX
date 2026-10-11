# LTX 소리 포함 영상

2026-10-11부터 공개본에 ComfyUI 이미지·영상 연결을 포함합니다. 모델 가중치와 ComfyUI 자체는 별도로 설치합니다.

## 설정과 사용

ComfyUI portable 폴더(ComfyUI/와 python_embeded/를 포함)를 `JARVIS_COMFYUI_DIR` 또는 로컬 설정 `comfyui_dir`로 지정합니다. `JARVIS_IMAGE_GENERATION_ENABLED=1` 또는 `image_generation_enabled=true`로 생성 기능을 켭니다. 기본 연결은 이 PC의 `http://127.0.0.1:8188`입니다.

ComfyUI 모델 폴더에는 다음 LTX 파일이 필요합니다. 각 배포처의 이용 조건을 확인해 설치합니다.

| 폴더 | 파일 |
|---|---|
| diffusion_models | ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors |
| text_encoders | gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors |
| vae | ltx-2.5-video-vae-bf16.safetensors |
| vae | ltx-2.5-audio-vae-bf16.safetensors |
| latent_upscale_models | ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors |

- 직접 프롬프트 창: 생성 종류 **영상**, 모델 **LTX-2.5**를 선택합니다.
- CLI: `/video-prompt --model=ltx A cat walks in a garden. Birds chirp.`
- 자연어: `새 소리가 나는 고양이 영상 만들어줘`.
- 참고 그림 뒤: `방금 그림의 여기사가 검을 들어 올리는 영상 만들어줘. 바람 소리와 갑옷이 찰랑거리는 소리를 넣어줘.`

자동 선택은 소리·음악·대사 요청에 LTX를 사용하고, 명시적으로 고른 Wan/LTX가 우선합니다. LTX 파일이 없으면 Wan으로 바꾸면서 소리 미포함을 알립니다. Wan에도 필요한 별도 모델 파일이 있어야 합니다.

## 동작과 실측

LTX는 2단계 생성·잠재 확대와 영상/음성 디코드를 거쳐 MP4로 저장합니다. 약5초·24fps·121프레임이며 가로1280×704, 세로704×1280, 정사각960×960입니다. 별도 SeedVR2 FHD 확대는 붙이지 않습니다.

RTX5080 16GB·RAM96GB의 단회 실측: 고양이 글→영상68.4초, 여기사 그림→영상101.8초(설명 번역 포함, 영상 생성 약69초). 안내 약2분은 이 조건의 추정치입니다. LTX 자연어 설명은1600자까지 보존해 뒤쪽 오디오 지시 누락을 방지합니다.

생성 전에 로컬 대화 모델의 GPU 반환을 확인합니다. 생성과 대화를 동시에 실행하지 않고, 완료/실패/취소 후 GPU 메모리를 반환합니다. 로컬 실측에서는 GPU 최고69도, VRAM 최고15652MiB였습니다. 이 결과는 장시간 안정성이나 모든 장면 지시의 정확성을 보장하는 벤치마크가 아닙니다. 최종 여기사 영상의 검 올리기 동작은 약했고, 오디오 의미·동기화는 별도 청취 평가를 하지 않았습니다.

관련 재현 검사: `python -W error::ResourceWarning -m unittest tests.test_ltx_video tests.test_imagegen tests.test_media_shutdown_contracts`. 전체 공개본 검사: `python scripts/verify_release.py`. 실모델을 호출하지 않습니다.
