# What is switched on. Committed on purpose (nothing sensitive): every plan sees the full
# intended state, so a plan run without flags never destroys a deployed piece.
ec2_enabled   = true
api_enabled   = true
api_image_tag = "" # set by scripts/deploy_api.sh's output after an image is pushed
