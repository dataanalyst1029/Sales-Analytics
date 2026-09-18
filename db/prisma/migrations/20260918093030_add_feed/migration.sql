/*
  Warnings:

  - Added the required column `feed` to the `transaction` table without a default value. This is not possible if the table is not empty.

*/
-- AlterTable
ALTER TABLE "transaction" ADD COLUMN     "feed" TEXT NOT NULL;

-- CreateIndex
CREATE INDEX "transaction_feed_idx" ON "transaction"("feed");
