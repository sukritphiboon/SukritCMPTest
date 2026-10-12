# วงจรชีวิตของ OceanProtect ตามเอกสาร REST API

สรุปจากเอกสาร *OceanProtect Appliance V200R001C32 REST Interface Reference* ว่า API รองรับอะไรบ้าง
และ CMP ทำไปถึงไหนแล้ว (ภาพทั้งหมดสร้างจาก `docs/diagrams/*.svg`)

## 1. ภาพรวม 6 ช่วง

![ภาพรวมวงจรชีวิต](diagrams/01-lifecycle-overview.png)

เตรียมระบบ → กำหนดการปกป้อง (SLA) → สำรอง → สำเนา → ใช้สำเนา → หมดอายุ/ลบ
โดยมีชั้นความปลอดภัย (WORM, ransomware, air gap, DR drill) คร่อมอยู่ด้านล่าง

จุดสำคัญ: **ตารางเวลาอยู่ใน SLA ของเครื่อง** เครื่องรันเองตาม SLA
ส่วนการสั่งสำรองด้วยมือเป็นทางเลือกเสริม

## 2. สถานะของงาน (job)

![สถานะของ job](diagrams/02-job-states.png)

เอกสารให้ค่าสถานะ 11 ค่า แต่ไม่ได้วาดลำดับการเปลี่ยนสถานะ ลูกศรเส้นประในภาพจึงเป็นการอนุมานของเรา
ค่า `ABORT_FAILED` ยังไม่มีสถานะรองรับใน CMP

## 3. ชีวิตของสำเนา

![ชีวิตของสำเนา](diagrams/03-copy-lifecycle.png)

## 4. CMP ทำไปถึงไหน

![CMP เทียบวงจรชีวิต](diagrams/04-cmp-coverage.png)

สีเขียวคือใช้ต่อได้ทันที สีเหลืองคือมีแล้วแต่ต้องเขียน driver ใหม่ให้ตรง API จริง สีเทาคือยังไม่มี
ขั้นต่อไปที่ตกลงกันไว้คือสร้าง mock และ driver ใหม่บน API จริง (`/v1`, `X-Auth-Token`)

## สิ่งที่ยังไม่ยืนยัน

- อ่านถึงระดับรายชื่อ interface ของ replication, archive, live mount, ransomware, air gap, DR drill
- ลำดับการเปลี่ยนสถานะของ job และ copy เป็นการอนุมาน
- ไม่พบค่า enum สถานะของ copy และค่า `generated_by` ในส่วนที่อ่าน
- การหยุดงานใช้ `PUT /v1/jobs/{jobId}/action/stop` (ไม่ใช่ POST)
