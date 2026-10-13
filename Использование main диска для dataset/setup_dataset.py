#!/usr/bin/env python3
#Проверяем диски

fdisk -l

#Создаём ZFS-раздел на оставшийся объём

sgdisk -n4:0:0 -t4:BF01 /dev/sda
sgdisk -n4:0:0 -t4:BF01 /dev/sdb
partprobe

#Создаём зеркальный пул

zpool create data_pool mirror /dev/sda4 /dev/sdb4

#или через PARTUUID

blkid /dev/sda4
blkid /dev/sdb4

zpool create -f datapool mirror /dev/disk/by-partuuid/[UUIDsda4] /dev/disk/by-partuuid/[UUID_sdb4]

#Экспорт и импорт пула

zpool export data_pool

#в WEB интерфейсе → Storage → Import Pool → data\_pool

#Команды для восстановления разделов SSD


sudo partx -v -s /dev/sde
sudo partx -v -s /dev/sdf

#Отключаем, а затем удаляем раздел от загрузочного пула

zpool detach boot-pool sde3
sgdisk -d 3 /dev/sde

#Создаем новый третий раздел в 32ГБ и новый четвертый раздел из оставшегося объема

sgdisk -n 3:0:+32GiB -t3:BF01 /dev/sde
sgdisk -n4:0:0 -t4:BF01 /dev/sde

#Подключвем третий раздел sde3 к загрузочному диску boot-pool

zpool attach boot-pool /dev/sdf3 /dev/sde3

#Проверка статуса пула data pool

zpool status data_pool
sudo partx -v -s /dev/sde

#Добавляем раздел в существующее зеркало data pool

zpool replace [имя пула] [имя недоступного диска] /dev/disk/by-partuuid/[UUID четвертого раздела на новом SSD]